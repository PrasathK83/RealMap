from flask import Flask, jsonify, render_template_string, Response, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import threading
import time
import json
import os
import sys
import requests
from urllib.parse import urlparse
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
import ssl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import NetworkScanner
from topology import TopologyManager
from monitor import ChangeMonitor
from database import NetworkDatabase


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


def _create_socketio(flask_app):
    preferred_mode = os.getenv('REALMAP_ASYNC_MODE')
    if preferred_mode:
        candidates = [preferred_mode]
    elif sys.version_info >= (3, 13):
        # Eventlet is currently unstable on newer Python runtimes in many hosts.
        candidates = ['threading', 'gevent', 'eventlet']
    else:
        candidates = ['eventlet', 'gevent', 'threading']
    last_error = None

    for mode in candidates:
        try:
            instance = SocketIO(flask_app, cors_allowed_origins='*', async_mode=mode)
            print(f"[RealMap] SocketIO async mode: {instance.async_mode}")
            return instance
        except Exception as exc:
            last_error = exc
            print(f"[RealMap] Failed to init SocketIO with mode '{mode}': {exc}")

    raise RuntimeError(f"Unable to initialize SocketIO. Last error: {last_error}")

app = Flask(__name__, template_folder='.', static_folder='.')
CORS(app)
socketio = _create_socketio(app)

IS_CLOUD_ENV = _env_flag('REALMAP_CLOUD_MODE', default=False) or bool(os.getenv('RENDER'))
ENABLE_SCANNING = _env_flag('REALMAP_ENABLE_SCANNING', default=not IS_CLOUD_ENV)
ENABLE_BACKGROUND_SCANNER = _env_flag('REALMAP_ENABLE_BACKGROUND_SCANNER', default=not IS_CLOUD_ENV)
ENABLE_ACTIVE_PROBES = _env_flag('REALMAP_ENABLE_ACTIVE_PROBES', default=not IS_CLOUD_ENV)
PORT_SCAN_TIMEOUT_SECONDS = float(os.getenv('REALMAP_PORT_SCAN_TIMEOUT', '2.0'))

scanner = NetworkScanner()
topology = TopologyManager()
monitor = ChangeMonitor()
db = NetworkDatabase()

scan_lock = threading.Lock()
is_scanning = False
SCAN_INTERVAL_SECONDS = 15
last_device_list = []
network_health_score = 100
scanner_stop_event = threading.Event()
scan_job_lock = threading.Lock()
scan_job_active = False
scan_executor = ThreadPoolExecutor(max_workers=max(1, int(os.getenv('REALMAP_SCAN_WORKERS', '2'))))


def _cloud_disabled_response(feature):
    return jsonify({
        'status': 'disabled',
        'feature': feature,
        'cloud_mode': IS_CLOUD_ENV,
        'message': f"{feature} is disabled in cloud mode. Set env var to enable if you understand the network restrictions."
    }), 503


def _mark_scan_job_complete():
    global scan_job_active
    with scan_job_lock:
        scan_job_active = False


def _submit_scan_job():
    global scan_job_active
    with scan_job_lock:
        if scan_job_active:
            return False
        scan_job_active = True

    future = scan_executor.submit(background_scanner_once)
    future.add_done_callback(lambda _: _mark_scan_job_complete())
    return True


def _perform_scan_cycle():
    global network_health_score

    if not ENABLE_SCANNING:
        payload = scanner.get_demo_topology()
        payload.setdefault('changes', [])
        payload.setdefault('health', network_health_score)
        payload['scan_mode'] = 'degraded'
        payload['message'] = 'Active network scan is disabled in cloud mode.'
        payload['timestamp'] = time.strftime('%H:%M:%S')
        topology.save_snapshot(payload)
        socketio.emit('topology_update', payload)
        return payload

    print('[RealMap] Starting network scan...')
    devices = scanner.discover_devices()
    links = scanner.measure_links(devices)
    graph = topology.build_graph(devices, links)
    changes = monitor.detect_changes(devices)

    stats = topology.get_stats(graph)
    network_health_score = calculate_network_health(devices, stats)

    track_device_changes(last_device_list, devices)
    db.log_network_stats(
        stats.get('total_devices', 0),
        stats.get('online', 0),
        stats.get('avg_latency', 0),
        network_health_score
    )

    payload = {
        'nodes': devices,
        'links': links,
        'stats': stats,
        'changes': changes,
        'health': network_health_score,
        'scan_mode': 'active',
        'timestamp': time.strftime('%H:%M:%S')
    }
    topology.save_snapshot(payload)
    socketio.emit('topology_update', payload)
    print(f"[RealMap] Scan complete. {len(devices)} devices found. Health: {network_health_score}%")
    return payload

def calculate_network_health(devices, stats):
    """Calculate network health score (0-100)."""
    if not devices:
        return 50
    
    score = 100
    
    # Deduct for offline devices
    offline_count = sum(1 for d in devices if d.get('status') == 'offline')
    score -= offline_count * 5
    
    # Deduct for warning devices
    warning_count = sum(1 for d in devices if d.get('status') == 'warning')
    score -= warning_count * 2
    
    # Deduct for high latency
    avg_latency = stats.get('avg_latency', 0)
    if avg_latency > 100:
        score -= 20
    elif avg_latency > 50:
        score -= 10
    
    # Deduct if connectivity issues
    total = len(devices)
    online = stats.get('online', 0)
    if total > 0 and (online / total) < 0.8:
        score -= 15
    
    return max(0, min(100, score))

def track_device_changes(old_devices, new_devices):
    """Track and alert on device status changes."""
    global last_device_list
    
    old_ips = {d['ip'] for d in old_devices}
    new_ips = {d['ip'] for d in new_devices}
    
    # Devices that joined
    joined = new_ips - old_ips
    for ip in joined:
        device = next((d for d in new_devices if d['ip'] == ip), None)
        if device:
            msg = f"{device.get('user', 'Device')} ({device['ip']}) joined the network"
            db.add_alert('device_join', ip, device.get('user', 'Unknown'), msg, 'info')
            socketio.emit('new_alert', {
                'type': 'device_join',
                'device': device.get('user', 'Device'),
                'message': msg,
                'severity': 'info'
            })
            print(f"[RealMap] ✓ {msg}")
    
    # Devices that left
    left = old_ips - new_ips
    for ip in left:
        device = next((d for d in old_devices if d['ip'] == ip), None)
        if device:
            msg = f"{device.get('user', 'Device')} ({device['ip']}) left the network"
            db.add_alert('device_leave', ip, device.get('user', 'Unknown'), msg, 'warning')
            socketio.emit('new_alert', {
                'type': 'device_leave',
                'device': device.get('user', 'Device'),
                'message': msg,
                'severity': 'warning'
            })
            print(f"[RealMap] ✗ {msg}")
    
    # Log all device statuses
    for device in new_devices:
        db.log_device_status(
            device['ip'],
            device.get('mac', ''),
            device.get('user', ''),
            device.get('hostname', ''),
            device.get('type', ''),
            device.get('status', 'online')
        )
    
    last_device_list = new_devices

def background_scanner():
    """Background thread: scans network every SCAN_INTERVAL_SECONDS and pushes updates."""
    global is_scanning
    while not scanner_stop_event.is_set():
        try:
            with scan_lock:
                is_scanning = True
            _perform_scan_cycle()

        except Exception as e:
            print(f"[RealMap] Scan error: {e}")
        finally:
            with scan_lock:
                is_scanning = False
        scanner_stop_event.wait(SCAN_INTERVAL_SECONDS)


@app.route('/')
def index():
    with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
        return f.read()


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)


@app.route('/api/topology')
def get_topology():
    snapshot = topology.get_latest_snapshot()
    if snapshot:
        return jsonify(snapshot)
    # Return demo data if no scan done yet
    return jsonify(scanner.get_demo_topology())


@app.route('/api/scan', methods=['POST'])
def trigger_scan():
    if not ENABLE_SCANNING:
        return _cloud_disabled_response('network_scan')
    if is_scanning or scan_job_active:
        return jsonify({'status': 'already_scanning'})
    if not _submit_scan_job():
        return jsonify({'status': 'already_scanning'})
    return jsonify({'status': 'scan_started'})


@app.route('/api/history')
def get_history():
    return jsonify(topology.get_history())


@app.route('/api/devices/<ip>')
def get_device(ip):
    return jsonify(scanner.get_device_details(ip))


@app.route('/api/alerts')
def get_alerts():
    """Get recent network alerts."""
    limit = request.args.get('limit', 50, type=int)
    return jsonify(db.get_alerts(limit))


@app.route('/api/health')
def get_health():
    """Get current network health score."""
    return jsonify({
        'health': network_health_score,
        'timestamp': time.strftime('%H:%M:%S')
    })


@app.route('/api/stats')
def get_stats():
    """Get network statistics and trends."""
    hours = request.args.get('hours', 24, type=int)
    stats = db.get_network_stats(hours)
    return jsonify({
        'current': {
            'health': network_health_score,
            'timestamp': time.strftime('%H:%M:%S')
        },
        'history': stats
    })


@app.route('/api/device/<ip>/history')
def get_device_history(ip):
    """Get status history for a device."""
    days = request.args.get('days', 7, type=int)
    uptime = db.get_uptime_percentage(ip, days)
    history = db.get_device_history(ip, days)
    return jsonify({
        'ip': ip,
        'uptime_percentage': uptime,
        'history': history
    })


@app.route('/api/config', methods=['GET', 'POST'])
def config():
    """Get/Set configuration."""
    global SCAN_INTERVAL_SECONDS
    if request.method == 'POST':
        data = request.json
        if 'scan_interval' in data:
            SCAN_INTERVAL_SECONDS = int(data['scan_interval'])
            return jsonify({'status': 'updated', 'scan_interval': SCAN_INTERVAL_SECONDS})
        return jsonify({'error': 'Invalid config'})
    return jsonify({
        'scan_interval': SCAN_INTERVAL_SECONDS,
        'cloud_mode': IS_CLOUD_ENV,
        'scanning_enabled': ENABLE_SCANNING,
        'background_scanner_enabled': ENABLE_BACKGROUND_SCANNER,
        'active_probes_enabled': ENABLE_ACTIVE_PROBES
    })


@app.route('/api/ips')
def get_available_ips():
    """Get list of available IPs from the last topology scan."""
    snapshot = topology.get_latest_snapshot()
    if snapshot and 'nodes' in snapshot:
        ips = [
            {
                'ip': node.get('ip'),
                'hostname': node.get('user', node.get('hostname', 'Unknown')),
                'status': node.get('status', 'offline')
            }
            for node in snapshot['nodes']
        ]
        return jsonify({'status': 'success', 'ips': ips})
    # Return demo IPs if no scan done yet
    demo_data = scanner.get_demo_topology()
    if 'nodes' in demo_data:
        ips = [
            {
                'ip': node.get('ip'),
                'hostname': node.get('user', node.get('hostname', 'Unknown')),
                'status': node.get('status', 'offline')
            }
            for node in demo_data['nodes']
        ]
        return jsonify({'status': 'success', 'ips': ips})
    return jsonify({'status': 'error', 'message': 'No devices found'})


def scan_ports(ip, timeout=2):
    """Scan common ports on a given IP address."""
    open_ports = []
    closed_ports = []
    
    # Common ports to scan
    common_ports = [21, 22, 23, 25, 53, 80, 110, 143, 443, 445, 3306, 3389, 5432, 5900, 8080, 8443, 9000]
    
    def check_port(port):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            return (port, result == 0)
        except socket.error:
            return (port, False)
    
    # Use ThreadPoolExecutor for faster scanning
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_port, port): port for port in common_ports}
        for future in as_completed(futures):
            port, is_open = future.result()
            if is_open:
                open_ports.append(port)
            else:
                closed_ports.append(port)
    
    open_ports.sort()
    return open_ports, closed_ports


@app.route('/api/ports/<ip>')
def get_ports(ip):
    """Scan ports on a specific IP address."""
    if not ENABLE_ACTIVE_PROBES:
        return _cloud_disabled_response('port_scan')
    try:
        # Validate IP format
        socket.inet_aton(ip)
        
        open_ports, closed_ports = scan_ports(ip, timeout=PORT_SCAN_TIMEOUT_SECONDS)
        
        return jsonify({
            'status': 'success',
            'ip': ip,
            'open_ports': open_ports,
            'closed_ports': closed_ports,
            'total_scanned': len(open_ports) + len(closed_ports),
            'timestamp': time.strftime('%H:%M:%S')
        })
    except socket.error:
        return jsonify({'status': 'error', 'message': 'Invalid IP address'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


def grab_banner(ip, port, timeout=3):
    """Grab service banner from open port."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        banner = sock.recv(1024).decode('utf-8', errors='ignore').strip()
        sock.close()
        return banner if banner else 'Connection successful (no banner)'
    except Exception as e:
        return None


def get_ssl_info(ip, port, timeout=5):
    """Get SSL/TLS certificate info."""
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=ip) as ssock:
                cert = ssock.getpeercert()
                cipher = ssock.cipher()
                version = ssock.version()
                
                return {
                    'protocol': version,
                    'cipher': cipher[0] if cipher else 'Unknown',
                    'subject': cert.get('subject', []),
                    'issuer': cert.get('issuer', []),
                    'valid_from': cert.get('notBefore', 'Unknown'),
                    'valid_until': cert.get('notAfter', 'Unknown')
                }
    except Exception as e:
        return None


@app.route('/api/recon')
@app.route('/api/recon/<ip>')
def reconnaissance(ip=None):
    """Comprehensive reconnaissance on target IP."""
    if not ENABLE_ACTIVE_PROBES:
        return _cloud_disabled_response('recon')
    try:
        if not ip:
            ip = (request.args.get('target') or '').strip()
        if not ip:
            return jsonify({'status': 'error', 'message': 'target IP is required'}), 400
        socket.inet_aton(ip)
        
        # Port scan
        open_ports, _ = scan_ports(ip, timeout=2)
        
        # Service detection and banner grabbing
        services = {}
        for port in open_ports:
            banner = grab_banner(ip, port, timeout=2)
            ssl_info = None
            
            # If it's a web port, get SSL info
            if port in [443, 8443, 8000, 8080]:
                if port == 443 or port == 8443:
                    ssl_info = get_ssl_info(ip, port, timeout=3)
            
            services[port] = {
                'banner': banner,
                'ssl_info': ssl_info
            }
        
        # DNS resolution
        dns_info = None
        try:
            dns_info = socket.gethostbyaddr(ip)
        except:
            try:
                dns_info = socket.gethostbyname_ex(ip)
            except:
                dns_info = None
        
        return jsonify({
            'status': 'success',
            'ip': ip,
            'open_ports': open_ports,
            'services': services,
            'dns': dns_info,
            'timestamp': time.strftime('%H:%M:%S')
        })
    except socket.error:
        return jsonify({'status': 'error', 'message': 'Invalid IP address'}), 400
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/service-enum/<ip>/<int:port>')
def service_enumeration(ip, port):
    """Enumerate specific service on target."""
    if not ENABLE_ACTIVE_PROBES:
        return _cloud_disabled_response('service_enumeration')
    try:
        socket.inet_aton(ip)
        
        service_probes = {
            80: b'HEAD / HTTP/1.0\r\nHost: {}\r\nConnection: close\r\n\r\n',
            443: None,  # Will use SSL
            22: b'',  # SSH banner is automatic
            23: b'',  # Telnet banner
            25: b'EHLO recon\r\nQUIT\r\n',  # SMTP
            53: None,  # DNS (special handling)
            110: b'USER test\r\nQUIT\r\n',  # POP3
            143: b'A001 LOGOUT\r\n',  # IMAP
        }
        
        response = {}
        
        if port == 443 or port == 8443:
            response['ssl_certificate'] = get_ssl_info(ip, port, timeout=3)
        else:
            banner = grab_banner(ip, port, timeout=2)
            response['banner'] = banner
        
        return jsonify({
            'status': 'success',
            'ip': ip,
            'port': port,
            'service_info': response,
            'timestamp': time.strftime('%H:%M:%S')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/dns')
@app.route('/api/dns/<target>')
def dns_lookup(target=None):
    """DNS resolution and lookup."""
    if not ENABLE_ACTIVE_PROBES:
        return _cloud_disabled_response('dns_lookup')
    try:
        if not target:
            target = (request.args.get('target') or '').strip()
        if not target:
            return jsonify({'status': 'error', 'message': 'target is required'}), 400

        result = {
            'target': target,
            'a_records': [],
            'reverse': None,
            'mx_records': []
        }
        
        # Forward DNS
        try:
            a_records = socket.gethostbyname_ex(target)
            result['a_records'] = a_records[2] if a_records else []
        except:
            pass
        
        # Reverse DNS
        try:
            result['reverse'] = socket.gethostbyaddr(target)[0]
        except:
            pass
        
        # Try to resolve any IP
        try:
            ip = socket.gethostbyname(target)
            result['ip'] = ip
            result['reverse_ip'] = socket.gethostbyaddr(ip)[0]
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': time.strftime('%H:%M:%S')
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/share', methods=['POST'])
def share_data():
    """Send selected RealMap data via HTTP webhook or UDP broadcast."""
    if IS_CLOUD_ENV and not ENABLE_ACTIVE_PROBES:
        return _cloud_disabled_response('data_share')
    data = request.json or {}
    target = (data.get('webhook_url') or '').strip()
    protocol = (data.get('protocol') or 'http').strip().lower()
    payload_type = (data.get('payload_type') or 'topology').strip().lower()

    if not target:
        return jsonify({'status': 'error', 'message': 'webhook_url or target IP is required'}), 400

    # Build the outbound payload
    if payload_type == 'topology':
        payload = topology.get_latest_snapshot() or scanner.get_demo_topology()
    elif payload_type == 'alerts':
        payload = {'alerts': db.get_alerts(50)}
    elif payload_type == 'stats':
        payload = {
            'current': {
                'health': network_health_score,
                'timestamp': time.strftime('%H:%M:%S')
            },
            'history': db.get_network_stats(24)
        }
    else:
        return jsonify({'status': 'error', 'message': 'payload_type must be one of: topology, alerts, stats'}), 400

    outbound = {
        'source': 'realmap',
        'payload_type': payload_type,
        'sent_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'data': payload
    }

    if protocol == 'udp':
        return send_via_udp(target, outbound, payload_type)
    else:
        return send_via_http(target, outbound, payload_type)


def send_via_http(webhook_url, outbound, payload_type):
    """Send data via HTTP POST to webhook URL."""
    # Accept plain IP/host values (for example "192.168.1.50:8080/webhook").
    if not webhook_url.startswith('http://') and not webhook_url.startswith('https://'):
        webhook_url = f'http://{webhook_url}'

    parsed = urlparse(webhook_url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return jsonify({'status': 'error', 'message': 'Invalid webhook URL or IP format'}), 400

    try:
        response = requests.post(webhook_url, json=outbound, timeout=10)
        return jsonify({
            'status': 'sent',
            'http_status': response.status_code,
            'payload_type': payload_type,
            'target': webhook_url,
            'protocol': 'http'
        })
    except requests.RequestException as e:
        return jsonify({'status': 'error', 'message': f'Failed to send webhook: {str(e)}'}), 502


def send_via_udp(target, outbound, payload_type):
    """Send data via UDP broadcast to target IP:port."""
    try:
        # Parse target IP:port
        if ':' in target:
            ip, port_str = target.rsplit(':', 1)
            try:
                port = int(port_str)
            except ValueError:
                return jsonify({'status': 'error', 'message': 'Invalid port number in target'}), 400
        else:
            ip = target
            port = 5533  # Default UDP broadcast port

        # Send JSON payload via UDP
        payload_json = json.dumps(outbound)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(5)
        sock.sendto(payload_json.encode('utf-8'), (ip, port))
        sock.close()

        return jsonify({
            'status': 'sent',
            'protocol': 'udp',
            'target': f'{ip}:{port}',
            'payload_type': payload_type,
            'message': f'UDP packet sent to {ip}:{port}'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to send UDP packet: {str(e)}'}), 502


def background_scanner_once():
    global is_scanning
    if not ENABLE_SCANNING:
        _perform_scan_cycle()
        return
    try:
        with scan_lock:
            is_scanning = True
        _perform_scan_cycle()
    except Exception as e:
        print(f"[RealMap] Scan error: {e}")
    finally:
        with scan_lock:
            is_scanning = False


@socketio.on('connect')
def handle_connect():
    print('[RealMap] Client connected')
    snapshot = topology.get_latest_snapshot()
    if snapshot:
        emit('topology_update', snapshot)
    else:
        emit('topology_update', scanner.get_demo_topology())


@socketio.on('request_scan')
def handle_scan_request():
    print('[RealMap] Received request_scan event')
    if not ENABLE_SCANNING:
        emit('scan_error', {'message': 'Scanning is disabled in cloud mode'})
        return
    try:
        if not _submit_scan_job():
            emit('scan_error', {'message': 'A scan is already in progress'})
            return
        emit('scan_started', {'message': 'Scanning network...'})
        print('[RealMap] Sent scan_started event to client')
    except Exception as e:
        print(f'[RealMap] Error in handle_scan_request: {e}')
        emit('scan_error', {'message': f'Error starting scan: {str(e)}'})


if __name__ == '__main__':
    if ENABLE_BACKGROUND_SCANNER:
        bg_thread = threading.Thread(target=background_scanner, daemon=True)
        bg_thread.start()
        print('[RealMap] Background scanner enabled')
    else:
        print('[RealMap] Background scanner disabled')

    print("\n" + "="*50)
    print("  🗺️  RealMap — Network Topology Engine")
    run_port = int(os.getenv('PORT', '5000'))
    print(f"  Open: http://localhost:{run_port}")
    print(f"  Cloud mode: {'on' if IS_CLOUD_ENV else 'off'}")
    print(f"  Scanning enabled: {'yes' if ENABLE_SCANNING else 'no'}")
    print("="*50 + "\n")

    run_kwargs = {
        'host': '0.0.0.0',
        'port': run_port,
        'debug': False,
    }
    if IS_CLOUD_ENV and socketio.async_mode == 'threading':
        # Safety valve when platform start command still uses `python app.py`.
        run_kwargs['allow_unsafe_werkzeug'] = True
    socketio.run(app, **run_kwargs)
