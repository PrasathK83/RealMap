import subprocess
import socket
import struct
import platform
import random
import time
import os
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import scapy.all as scapy
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

try:
    import nmap
    NMAP_AVAILABLE = True
except ImportError:
    NMAP_AVAILABLE = False


class NetworkScanner:
    def __init__(self):
        self.subnet = self._detect_subnet()
        self.device_cache = {}
        self.hostname_cache = {}
        self.os_cache = {}
        self.fast_scan = os.getenv('REALMAP_FAST_SCAN', '1') != '0'
        self.resolve_hostnames = os.getenv('REALMAP_RESOLVE_HOSTNAMES', '1') == '1'
        self.enable_os_fingerprint = os.getenv('REALMAP_OS_FINGERPRINT', '0') == '1'
        self.router_count = 0
        self.switch_count = 0

    def _detect_subnet(self):
        """Auto-detect local subnet."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            parts = local_ip.split('.')
            return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
        except:
            return "192.168.1.0/24"

    def discover_devices(self):
        """Discover all devices on the network."""
        # Reset counters for new scan
        self.router_count = 0
        self.switch_count = 0
        
        if SCAPY_AVAILABLE:
            return self._scapy_scan()
        else:
            return self._ping_sweep()

    def _scapy_scan(self):
        """ARP scan using Scapy."""
        try:
            arp_request = scapy.ARP(pdst=self.subnet)
            broadcast   = scapy.Ether(dst="ff:ff:ff:ff:ff:ff")
            packet      = broadcast / arp_request
            answered, _ = scapy.srp(packet, timeout=1.5 if self.fast_scan else 3, verbose=False)

            devices = []
            
            def enrich_device(received):
                cached = self.device_cache.get(received.psrc, {})
                # Initial quick guess based on IP/MAC only
                device_type = self._guess_device_type(received.hwsrc, received.psrc)
                
                # Resolve hostname
                if device_type == 'router':
                    resolved_hostname = self._resolve_router_hostname(received.psrc)
                    if not resolved_hostname:
                        resolved_hostname = self._resolve_hostname(received.psrc, cached.get('hostname'))
                else:
                    resolved_hostname = self._resolve_hostname(received.psrc, cached.get('hostname'))
                
                # Refine device type after hostname resolution
                if resolved_hostname and resolved_hostname != received.psrc:
                    device_type = self._guess_device_type_enhanced(received.hwsrc, received.psrc, resolved_hostname)
                
                device = {
                    'ip':       received.psrc,
                    'mac':      received.hwsrc,
                    'hostname': self._friendly_hostname(received.psrc, device_type, resolved_hostname),
                    'user':     self._get_device_owner(received.hwsrc, device_type, resolved_hostname),
                    'type':     device_type,
                    'latency':  self._ping(received.psrc),
                    'status':   'online',
                    'os':       self._resolve_os(received.psrc, device_type, resolved_hostname, cached.get('os'))
                }
            
                return device
            
            responses = [received for _, received in answered]

            workers = 40 if self.fast_scan else 20
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(enrich_device, received) for received in responses]
                for future in as_completed(futures):
                    device = future.result()
                    devices.append(device)
                    self.device_cache[device['ip']] = device
            return devices
        except Exception as e:
            print(f"Scapy scan error: {e}")
            return self._ping_sweep()

    def _ping_sweep(self):
        """Fallback ping sweep when Scapy unavailable."""
        network = ipaddress.IPv4Network(self.subnet, strict=False)
        live_hosts = []

        def check_host(ip):
            ip_str = str(ip)
            latency = self._ping(ip_str)
            if latency is not None:
                cached = self.device_cache.get(ip_str, {})
                mac_addr = self._get_mac(ip_str)
                # Initial device type guess
                device_type = self._guess_device_type(mac_addr, ip_str)
                # Resolve hostname
                resolved_hostname = self._resolve_hostname(ip_str, cached.get('hostname'))
                # Refine device type after hostname resolution
                if resolved_hostname and resolved_hostname != ip_str:
                    device_type = self._guess_device_type_enhanced(mac_addr, ip_str, resolved_hostname)
                
                return {
                    'ip':       ip_str,
                    'mac':      mac_addr,
                    'hostname': self._friendly_hostname(ip_str, device_type, resolved_hostname),
                    'user':     self._get_device_owner(mac_addr, device_type, resolved_hostname),
                    'type':     device_type,
                    'latency':  latency,
                    'os':       self._resolve_os(ip_str, device_type, resolved_hostname, cached.get('os')),
                    'status':   'online'
                }
            return None

        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = {executor.submit(check_host, ip): ip for ip in list(network.hosts())[:254]}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    live_hosts.append(result)
                    self.device_cache[result['ip']] = result

        return live_hosts

    def _ping(self, ip):
        """Ping host and return latency in ms."""
        try:
            if platform.system().lower() == 'windows':
                cmd = ['ping', '-n', '1', '-w', '500' if self.fast_scan else '700', ip]
            else:
                cmd = ['ping', '-c', '1', '-W', '1', ip]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1.2 if self.fast_scan else 3)
            output = result.stdout

            if 'time=' in output:
                time_str = output.split('time=')[1].split()[0].replace('ms', '')
                return round(float(time_str), 2)
            elif 'time<' in output:
                return 0.5
        except:
            pass
        return None

    def _resolve_hostname(self, ip, fallback=None):
        """Resolve hostname from IP using DNS and platform fallbacks."""
        if fallback and fallback != ip:
            return fallback

        cached = self.hostname_cache.get(ip)
        if cached:
            return cached

        if not self.resolve_hostnames:
            return ip

        try:
            host = self._clean_hostname(socket.gethostbyaddr(ip)[0])
            if host and host != ip:
                self.hostname_cache[ip] = host
                return host
        except:
            pass

        if self.fast_scan:
            return fallback if fallback else ip

        host = self._resolve_hostname_with_commands(ip)
        if host and host != ip:
            self.hostname_cache[ip] = host
            return host

        return fallback if fallback else ip

    def _resolve_hostname_with_commands(self, ip):
        """Try system tools for hostname resolution when DNS PTR is unavailable."""
        host = self._resolve_with_nslookup(ip)
        if host:
            return host

        if platform.system().lower() == 'windows':
            host = self._resolve_with_nbtstat(ip)
            if host:
                return host

        return None

    def _get_gateway_ip(self):
        """Get default gateway IP."""
        try:
            if platform.system().lower() == 'windows':
                result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=2)
                for line in result.stdout.splitlines():
                    if 'default gateway' in line.lower():
                        parts = line.split(':')
                        if len(parts) > 1:
                            return parts[-1].strip()
            else:
                result = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=2)
                for line in result.stdout.splitlines():
                    if 'default via' in line:
                        parts = line.split()
                        for i, p in enumerate(parts):
                            if p == 'via' and i + 1 < len(parts):
                                return parts[i + 1]
        except:
            pass
        return None

    def _resolve_router_hostname(self, ip):
        """Specialized router hostname resolution using gateway detection."""
        try:
            gw_ip = self._get_gateway_ip()
            if gw_ip and gw_ip.strip() == ip:
                common_names = ['gateway.local', 'router.local', 'home.local', 'gateway', 'router']
                for name in common_names:
                    try:
                        resolved = socket.gethostbyname(name)
                        if resolved == ip:
                            return name
                    except:
                        pass
        except:
            pass
        return None

    def _resolve_with_nslookup(self, ip):
        try:
            result = subprocess.run(
                ['nslookup', ip],
                capture_output=True,
                text=True,
                timeout=1.2 if self.fast_scan else 2
            )
            for raw_line in result.stdout.splitlines():
                line = raw_line.strip()
                if line.lower().startswith('name:'):
                    candidate = self._clean_hostname(line.split(':', 1)[1].strip())
                    if candidate and candidate != ip:
                        return candidate
        except:
            pass
        return None

    def _resolve_with_nbtstat(self, ip):
        try:
            result = subprocess.run(
                ['nbtstat', '-A', ip],
                capture_output=True,
                text=True,
                timeout=1.5 if self.fast_scan else 2.5
            )
            for raw_line in result.stdout.splitlines():
                line = raw_line.strip()
                if '<00>' in line and 'UNIQUE' in line and 'GROUP' not in line:
                    name = line.split('<00>')[0].strip()
                    candidate = self._clean_hostname(name)
                    if candidate and candidate != ip and candidate.upper() != 'WORKGROUP':
                        return candidate
        except:
            pass
        return None

    def _clean_hostname(self, name):
        if not name:
            return None
        cleaned = str(name).strip().strip('.')
        if not cleaned or cleaned.lower() == 'unknown':
            return None
        return cleaned

    def _friendly_hostname(self, ip, device_type, resolved_hostname=None):
        """Return friendly name for devices."""
        # For routers and switches, use friendly names
        if device_type == 'router':
            if resolved_hostname and resolved_hostname != ip and not resolved_hostname.startswith('router'):
                return resolved_hostname  # Use actual hostname if available
            self.router_count += 1
            return f"Router" if self.router_count == 1 else f"Router-{self.router_count}"
        
        if device_type == 'switch':
            if resolved_hostname and resolved_hostname != ip and not resolved_hostname.startswith('switch'):
                return resolved_hostname  # Use actual hostname if available
            self.switch_count += 1
            return f"Switch-{self.switch_count}"
        
        # For other devices, use resolved hostname or IP
        if resolved_hostname and resolved_hostname != ip:
            return resolved_hostname
        return ip

    def _resolve_os(self, ip, device_type, hostname=None, fallback=None):
        """Get OS label for every device using fast heuristic + optional deep fingerprint."""
        cached = self.os_cache.get(ip)
        if cached and cached != 'Unknown':
            return cached

        os_name = self._guess_os_fast(device_type, hostname)

        if NMAP_AVAILABLE and self.enable_os_fingerprint and not self.fast_scan:
            fingerprinted = self._get_os(ip)
            if fingerprinted and fingerprinted != 'Unknown':
                os_name = fingerprinted

        if (not os_name or os_name == 'Unknown') and fallback and fallback != 'Unknown':
            os_name = fallback

        if not os_name or os_name == 'Unknown':
            os_name = 'Network Device OS'

        self.os_cache[ip] = os_name
        return os_name

    def _guess_os_fast(self, device_type, hostname):
        """Fast OS estimation without expensive scans."""
        name = (hostname or '').lower()

        # Check hostname for specific OS indicators
        if 'iphone' in name or 'ipad' in name or 'ios' in name:
            return 'iOS'
        if 'android' in name:
            return 'Android'
        if 'macbook' in name or 'imac' in name or 'mac' in name or 'apple' in name:
            return 'macOS'
        if 'win' in name or 'windows' in name or 'desktop-' in name or 'laptop-' in name or 'pc-' in name:
            return 'Windows'
        if 'ubuntu' in name or 'debian' in name or 'linux' in name or 'centos' in name or 'rhel' in name or 'fedora' in name:
            return 'Linux'
        
        # Device type based OS detection
        if device_type == 'router':
            # Routers typically run proprietary or Linux-based OS
            if 'tp-link' in name or 'netgear' in name or 'asus' in name or 'dlink' in name:
                return 'Router Firmware'
            return 'Router OS'
        
        if device_type == 'switch':
            # Switches typically run proprietary OS or Linux
            if 'cisco' in name or 'juniper' in name or 'arista' in name:
                return 'Switch OS'
            return 'Network OS'
        
        if device_type == 'server':
            # Servers are typically Linux or Windows
            if 'srv' in name or 'db' in name or 'web' in name or 'api' in name or 'mail' in name or 'dns' in name:
                # Common server patterns suggest Linux
                return 'Linux Server'
            # Default server OS
            return 'Server Linux/Windows'
        
        if device_type == 'computer':
            # Laptops and desktops
            return 'Windows/macOS/Linux'
        
        # Fallback for 'device' type - IoT and accessories
        if device_type == 'device':
            if any(x in name for x in ['camera', 'cam', 'video']):
                return 'Embedded OS'
            elif any(x in name for x in ['printer', 'print']):
                return 'Printer OS'
            elif any(x in name for x in ['speaker', 'audio', 'alexa', 'google', 'airplay']):
                return 'Smart Device OS'
            elif any(x in name for x in ['tv', 'chromecast', 'firestick', 'roku', 'android']):
                return 'Android TV/FireOS'
            elif any(x in name for x in ['watch', 'band', 'bracelet', 'wearable']):
                return 'Wearable OS'
            elif any(x in name for x in ['pi', 'raspberry', 'arduino', 'esp', 'microcontroller']):
                return 'Embedded Linux'
            else:
                return 'IoT OS'
        
        return 'Network Device OS'

    def _get_mac(self, ip):
        """Get MAC from ARP table."""
        try:
            if platform.system().lower() == 'windows':
                result = subprocess.run(['arp', '-a', ip], capture_output=True, text=True)
                for line in result.stdout.split('\n'):
                    if ip in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return parts[1]
            else:
                result = subprocess.run(['arp', '-n', ip], capture_output=True, text=True)
                for line in result.stdout.split('\n'):
                    if ip in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            return parts[2]
        except:
            pass
        return 'Unknown'

    def _get_os(self, ip):
        """OS fingerprinting with nmap."""
        try:
            nm = nmap.PortScanner()
            nm.scan(ip, arguments='-O --osscan-guess -T4', timeout=10)
            if ip in nm.all_hosts():
                osmatch = nm[ip].get('osmatch', [])
                if osmatch:
                    return osmatch[0]['name']
        except:
            pass
        return 'Unknown'

    def _guess_device_type(self, mac, ip):
        """Guess device type from MAC OUI or IP."""
        mac_upper = mac.upper().replace(':', '').replace('-', '')
        
        router_ouis = ['000C29', 'B827EB', '9C5F3A', 'D4CAB7']
        if any(mac_upper.startswith(oui) for oui in router_ouis):
            return 'router'

        ip_last = int(ip.split('.')[-1]) if ip else 0
        if ip_last == 1 or ip_last == 254:
            return 'router'
        elif ip_last < 10:
            return 'switch'
        elif ip_last < 50:
            return 'server'
        elif ip_last < 150:
            return 'computer'
        else:
            return 'device'

    def _guess_device_type_enhanced(self, mac, ip, hostname=None):
        """Enhanced device type detection using multiple heuristics."""
        mac_upper = mac.upper().replace(':', '').replace('-', '')
        hostname_lower = (hostname or '').lower()
        
        # Router detection
        router_ouis = ['000C29', 'B827EB', '9C5F3A', 'D4CAB7']
        if any(mac_upper.startswith(oui) for oui in router_ouis):
            return 'router'
        if any(x in hostname_lower for x in ['router', 'gateway', 'gw-', 'vpn-']):
            return 'router'
        
        # Switch detection
        if any(x in hostname_lower for x in ['switch', 'sw-', 'core', 'leaf', 'spine']):
            return 'switch'
        
        # Server detection
        if any(x in hostname_lower for x in ['server', 'srv-', 'web-', 'db-', 'api-', 'mail-', 'dns-', 'app-', 'db', 'sql']):
            return 'server'
        
        # Computer/Workstation detection
        if any(x in hostname_lower for x in ['workstation', 'desktop', 'laptop', 'pc-', 'computer', 'mac-', 'mbp-']):
            return 'computer'
        
        # IoT/Device detection
        if any(x in hostname_lower for x in ['printer', 'camera', 'tv-', 'iot-', 'sensor-', 'light-', 'thermostat']):
            return 'device'
        
        # IP-based fallback
        try:
            ip_last = int(ip.split('.')[-1]) if ip else 0
            if ip_last == 1 or ip_last == 254:
                return 'router'
            elif ip_last < 10:
                return 'switch'
            elif ip_last < 50:
                return 'server'
            elif ip_last < 150:
                return 'computer'
            else:
                return 'device'
        except:
            return 'device'

    def _get_device_owner(self, mac, device_type, hostname=None):
        """Map MAC address and device type to device owner/username."""
        mac_upper = mac.upper().replace(':', '').replace('-', '')
        
        # MAC prefix mappings for manufacturers
        mac_prefixes = {
            'A458': 'Apple',  # Apple devices
            '34AB': 'Apple',
            '7CFC': 'Apple',
            'F01F': 'Apple',
            'E0AC': 'Apple',
            '1C37': 'Apple',
            'AC87': 'Apple',
            
            '000C': 'VMware',  # Virtualization
            '005056': 'VMware',
            '000569': 'Dell',
            
            '3CB6': 'Realtek',  # Computers/Devices
            '0800': 'Xerox',
            'F4F5': 'Asus',
            
            'B827': 'Raspberry',  # Single board
            '9C39': 'Espressif',  # ESP32/IoT
            
            'C4DD': 'Brother',  # Printers
            '78A1': 'Canon',
            '1CBA': 'Hewlett',
        }
        
        # Device type based usernames
        device_owners = {
            'router': 'Router',
            'switch': 'Switch',
            'server': 'Server',
            'printer': 'Printer',
        }
        
        # Check MAC prefix for manufacturer hints
        for prefix, manufacturer in mac_prefixes.items():
            if mac_upper.startswith(prefix):
                if 'Apple' in manufacturer:
                    if device_type in ['computer', 'device']:
                        if 'iphone' in (hostname or '').lower():
                            return 'iPhone User'
                        elif 'ipad' in (hostname or '').lower():
                            return 'iPad User'
                        elif 'mac' in (hostname or '').lower():
                            return 'MacBook User'
                        else:
                            return 'Apple User'
                elif 'Brother' in manufacturer or 'Canon' in manufacturer or 'Hewlett' in manufacturer:
                    return 'Printer'
                elif 'Raspberry' in manufacturer:
                    return 'Raspberry Pi'
                elif 'Espressif' in manufacturer:
                    return 'IoT Device'
        
        # Fallback to device type
        if device_type in device_owners:
            return device_owners[device_type]
        
        # Generate based on hostname clues
        if hostname:
            hostname_lower = hostname.lower()
            if any(x in hostname_lower for x in ['server', 'web', 'db', 'api']):
                return 'Server'
            elif any(x in hostname_lower for x in ['laptop', 'desktop', 'workstation', 'pc']):
                return 'Computer'
            elif any(x in hostname_lower for x in ['printer', 'print']):
                return 'Printer'
            elif any(x in hostname_lower for x in ['phone', 'mobile', 'iphone', 'android']):
                return 'Mobile Device'
            elif any(x in hostname_lower for x in ['watch', 'band', 'bracelet', 'wearable']):
                return 'Wearable'
            elif any(x in hostname_lower for x in ['camera', 'cam', 'video']):
                return 'Camera'
            elif any(x in hostname_lower for x in ['speaker', 'audio', 'alexa', 'google']):
                return 'Smart Speaker'
            elif any(x in hostname_lower for x in ['tv', 'chromecast', 'firestick', 'roku']):
                return 'Smart TV'
        
        # Generic fallback
        type_names = {
            'router': 'Router',
            'switch': 'Switch',
            'server': 'Server',
            'computer': 'Computer',
            'device': 'Unknown Device'
        }
        return type_names.get(device_type, 'Device')

    def measure_links(self, devices):
        """Measure latency with merged topology: router directly connected to all devices (switches merged in)."""
        links = []
        if not devices:
            return links

        # Find router
        router = None
        for d in devices:
            if d['type'] == 'router':
                router = d
                break
        
        if not router:
            # Fallback: if no router, use first device as hub
            if devices:
                router = devices[0]
        
        # Connect all non-router devices directly to router
        # (switches are merged/hidden, their devices connect directly to router)
        for device in devices:
            if device['ip'] != router['ip']:
                latency = device.get('latency') or self._ping(device['ip']) or 0
                links.append({
                    'source':    router['ip'],
                    'target':    device['ip'],
                    'latency':   latency,
                    'bandwidth': self._estimate_bandwidth(latency),
                    'status':    'active'
                })
        
        return links

    def _estimate_bandwidth(self, latency):
        """Estimate bandwidth from latency (rough heuristic)."""
        if latency is None or latency == 0:
            return 1000
        elif latency < 2:
            return 1000
        elif latency < 10:
            return 100
        elif latency < 50:
            return 10
        else:
            return 1

    def get_device_details(self, ip):
        """Get detailed info for a specific device."""
        device = self.device_cache.get(ip, {'ip': ip})
        device['latency_history'] = [self._ping(ip) for _ in range(5)]
        device['open_ports'] = self._scan_ports(ip)
        return device

    def _scan_ports(self, ip, ports=[22, 80, 443, 8080, 3306, 5432]):
        """Quick port scan."""
        open_ports = []
        for port in ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex((ip, port))
                if result == 0:
                    open_ports.append(port)
                sock.close()
            except:
                pass
        return open_ports

    def get_demo_topology(self):
        """Returns a realistic demo topology for display when no scan done."""
        nodes = [
            {'ip': '192.168.1.1',   'mac': 'AA:BB:CC:DD:EE:01', 'hostname': 'router.local',    'user': 'Router',         'type': 'router',   'latency': 1.2,  'os': 'Linux', 'status': 'online'},
            {'ip': '192.168.1.10',  'mac': 'AA:BB:CC:DD:EE:10', 'hostname': 'web-server',       'user': 'Server',         'type': 'server',   'latency': 3.5,  'os': 'Ubuntu 22.04', 'status': 'online'},
            {'ip': '192.168.1.11',  'mac': 'AA:BB:CC:DD:EE:11', 'hostname': 'db-server',        'user': 'Server',         'type': 'server',   'latency': 4.2,  'os': 'Debian 11', 'status': 'online'},
            {'ip': '192.168.1.50',  'mac': 'AA:BB:CC:DD:EE:50', 'hostname': 'workstation-01',   'user': 'Office User',    'type': 'computer', 'latency': 5.1,  'os': 'Windows 11', 'status': 'online'},
            {'ip': '192.168.1.51',  'mac': 'AA:BB:CC:DD:EE:51', 'hostname': 'workstation-02',   'user': 'Office User',    'type': 'computer', 'latency': 6.3,  'os': 'Windows 11', 'status': 'online'},
            {'ip': '192.168.1.52',  'mac': '34:AB:CD:EF:01:01', 'hostname': 'macbook-pro',      'user': 'MacBook User',   'type': 'computer', 'latency': 8.7,  'os': 'macOS 14', 'status': 'online'},
            {'ip': '192.168.1.100', 'mac': 'A4:58:12:AB:34:CD', 'hostname': 'iphone-user1',     'user': 'iPhone User',    'type': 'device',   'latency': 12.4, 'os': 'iOS 17', 'status': 'online'},
            {'ip': '192.168.1.101', 'mac': 'AA:BB:CC:DD:EE:A1', 'hostname': 'android-tablet',   'user': 'Mobile User',    'type': 'device',   'latency': 15.2, 'os': 'Android 13', 'status': 'online'},
            {'ip': '192.168.1.200', 'mac': '78:A1:23:45:67:89', 'hostname': 'unknown-device',   'user': 'IoT Device',      'type': 'device',   'latency': 22.1, 'os': 'Unknown', 'status': 'warning'},
        ]
        links = [
            {'source': '192.168.1.1',  'target': '192.168.1.10',  'latency': 3.5,  'bandwidth': 1000, 'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.11',  'latency': 4.2,  'bandwidth': 1000, 'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.50',  'latency': 5.1,  'bandwidth': 100,  'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.51',  'latency': 6.3,  'bandwidth': 100,  'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.52',  'latency': 8.7,  'bandwidth': 100,  'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.100', 'latency': 12.4, 'bandwidth': 54,   'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.101', 'latency': 15.2, 'bandwidth': 54,   'status': 'active'},
            {'source': '192.168.1.1',  'target': '192.168.1.200', 'latency': 22.1, 'bandwidth': 11,   'status': 'warning'},
        ]
        return {
            'nodes': nodes,
            'links': links,
            'stats': {
                'total_devices': len(nodes),
                'online': 8,
                'warnings': 1,
                'avg_latency': round(sum(n['latency'] for n in nodes) / len(nodes), 2),
                'links_count': len(links)
            },
            'changes': [],
            'timestamp': time.strftime('%H:%M:%S'),
            'demo': True
        }
