#!/usr/bin/env python3
"""
UDP File Receiver with Web Dashboard
Listens on UDP port 8080 AND provides a web viewer on HTTP port 8081
"""

import socket
import json
import base64
import os
import threading
from pathlib import Path
from flask import Flask, render_template_string, jsonify, send_file
from datetime import datetime

LISTEN_IP = '0.0.0.0'
UDP_PORT = 5533
HTTP_PORT = 8081
OUTPUT_DIR = 'received_files'

app = Flask(__name__)
transfers = {}
received_files = []
lock = threading.Lock()

def create_output_dir():
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

def receive_udp():
    """UDP listener in background thread."""
    create_output_dir()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_IP, UDP_PORT))
    
    print(f"\n[UDP Receiver] Listening on {LISTEN_IP}:{UDP_PORT}")
    print(f"[UDP Receiver] Files saved to: {os.path.abspath(OUTPUT_DIR)}")
    print(f"[UDP Receiver] Web dashboard at: http://localhost:{HTTP_PORT}")
    print("[UDP Receiver] Waiting for transfers...\n")
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                packet = json.loads(data.decode('utf-8'))
                packet_type = packet.get('type')
                
                with lock:
                    if packet_type == 'file':
                        filename = packet.get('filename', 'unknown')
                        chunk_idx = packet.get('chunk', 0)
                        total_chunks = packet.get('total_chunks', 1)
                        chunk_data = packet.get('data', '')
                        
                        if filename not in transfers:
                            transfers[filename] = {
                                'chunks': {},
                                'total_chunks': total_chunks,
                                'sender': addr[0],
                                'started': datetime.now().isoformat()
                            }
                        
                        try:
                            transfers[filename]['chunks'][chunk_idx] = base64.b64decode(chunk_data)
                        except Exception as e:
                            print(f"[UDP] Decode error for {filename} chunk {chunk_idx}: {e}")
                            continue
                        
                        received = len(transfers[filename]['chunks'])
                        print(f"[UDP] {filename}: {received}/{total_chunks} chunks from {addr[0]}")
                        
                        if received == total_chunks:
                            print(f"[UDP] Assembling {filename}...")
                            file_data = b''
                            for i in range(total_chunks):
                                if i in transfers[filename]['chunks']:
                                    file_data += transfers[filename]['chunks'][i]
                            
                            output_path = os.path.join(OUTPUT_DIR, filename)
                            with open(output_path, 'wb') as f:
                                f.write(file_data)
                            
                            file_info = {
                                'filename': filename,
                                'size': len(file_data),
                                'received': datetime.now().isoformat(),
                                'sender': addr[0],
                                'path': output_path
                            }
                            received_files.insert(0, file_info)
                            print(f"[UDP] ✓ File saved: {output_path} ({len(file_data)} bytes)\n")
                            del transfers[filename]
            
            except Exception as e:
                print(f"[UDP] Error: {e}")
    except KeyboardInterrupt:
        print("\n[UDP] Shutting down...")
    finally:
        sock.close()

# ── WEB INTERFACE ──

@app.route('/')
def dashboard():
    return render_template_string('''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UDP File Receiver — Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background: #0a0e27;
            color: #c8e6f0;
            font-family: 'Courier New', monospace;
            padding: 20px;
        }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { font-size: 28px; margin-bottom: 10px; color: #00d4ff; text-shadow: 0 0 10px rgba(0,212,255,0.5); }
        .status { display: flex; gap: 20px; margin: 20px 0; }
        .status-card {
            background: #0f2a38;
            border: 1px solid #0f3a50;
            border-radius: 4px;
            padding: 15px;
            flex: 1;
        }
        .status-card h3 { font-size: 11px; letter-spacing: 2px; text-transform: uppercase; color: #4a7a8a; margin-bottom: 8px; }
        .status-card .value { font-size: 32px; color: #00ff88; font-weight: bold; }
        .files-section { margin-top: 30px; }
        .files-section h2 { font-size: 14px; letter-spacing: 1px; text-transform: uppercase; color: #00d4ff; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #0f3a50; }
        .file-item {
            background: #0f2a38;
            border: 1px solid #0f3a50;
            border-left: 3px solid #00d4ff;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 3px;
        }
        .file-name { font-size: 13px; font-weight: bold; color: #00d4ff; }
        .file-meta { font-size: 10px; color: #4a7a8a; margin-top: 6px; }
        .file-size { display: inline-block; margin-right: 15px; }
        .file-sender { display: inline-block; margin-right: 15px; }
        .file-time { display: inline-block; }
        .empty { text-align: center; color: #4a7a8a; padding: 30px; }
        .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #00ff88; box-shadow: 0 0 5px #00ff88; margin-right: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <h1><span class="dot"></span>UDP File Receiver</h1>
        <p style="color: #4a7a8a; font-size: 11px;">Listening on 0.0.0.0:5533</p>
        
        <div class="status">
            <div class="status-card">
                <h3>Files Received</h3>
                <div class="value" id="file-count">0</div>
            </div>
            <div class="status-card">
                <h3>Total Data</h3>
                <div class="value" id="total-size">0 B</div>
            </div>
            <div class="status-card">
                <h3>Last Updated</h3>
                <div class="value" id="last-update" style="font-size: 14px;">—</div>
            </div>
        </div>

        <div class="files-section">
            <h2>Received Files</h2>
            <div id="files-list"></div>
        </div>
    </div>

    <script>
        async function updateStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                document.getElementById('file-count').textContent = data.file_count;
                document.getElementById('total-size').textContent = formatBytes(data.total_size);
                document.getElementById('last-update').textContent = data.last_update || '—';
                
                const listHtml = data.files.length ? 
                    data.files.map(f => `
                        <div class="file-item">
                            <div class="file-name">📁 ${f.filename}</div>
                            <div class="file-meta">
                                <span class="file-size"><strong>Size:</strong> ${formatBytes(f.size)}</span>
                                <span class="file-sender"><strong>From:</strong> ${f.sender}</span>
                                <span class="file-time"><strong>Time:</strong> ${new Date(f.received).toLocaleString()}</span>
                            </div>
                        </div>
                    `).join('') :
                    '<div class="empty">No files received yet. Waiting...</div>';
                
                document.getElementById('files-list').innerHTML = listHtml;
            } catch (err) {
                console.error('Status update error:', err);
            }
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
        }

        setInterval(updateStatus, 2000);
        updateStatus();
    </script>
</body>
</html>
    ''')

@app.route('/api/status')
def api_status():
    with lock:
        files = list(received_files)
        total_size = sum(f['size'] for f in files)
        last_update = files[0]['received'] if files else None
    
    return jsonify({
        'file_count': len(files),
        'total_size': total_size,
        'last_update': last_update,
        'files': files
    })

if __name__ == '__main__':
    create_output_dir()
    
    # Start UDP listener in background
    udp_thread = threading.Thread(target=receive_udp, daemon=True)
    udp_thread.start()
    
    # Start Flask web server
    print(f"[Web] Starting dashboard on http://0.0.0.0:{HTTP_PORT}")
    app.run(host='0.0.0.0', port=HTTP_PORT, debug=False, use_reloader=False)
