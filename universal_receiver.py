#!/usr/bin/env python3
"""
Universal File Receiver
- Receives files via UDP on port 5533
- Displays files in web browser on port 5533 (HTTP)
- Recipients just open: http://localhost:5533 or http://their-ip:5533

Usage: python universal_receiver.py
"""

import socket
import json
import base64
import os
import threading
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

UDP_PORT = 5533
HTTP_PORT = 5534  # Can't use same port for UDP and HTTP
OUTPUT_DIR = 'received_files'

# Shared state
transfers = {}
received_files = []
lock = threading.Lock()

def create_output_dir():
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

def receive_udp():
    """Background UDP listener for file transfers."""
    create_output_dir()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', UDP_PORT))
    
    print(f"✓ UDP Receiver listening on port {UDP_PORT}")
    print(f"✓ Web interface at: http://localhost:{HTTP_PORT}")
    print(f"✓ Files saved to: {os.path.abspath(OUTPUT_DIR)}\n")
    
    while True:
        try:
            data, addr = sock.recvfrom(65535)
            packet = json.loads(data.decode('utf-8'))
            
            if packet.get('type') == 'file':
                filename = packet.get('filename', 'unknown')
                chunk_idx = packet.get('chunk', 0)
                total_chunks = packet.get('total_chunks', 1)
                chunk_data = packet.get('data', '')
                
                with lock:
                    if filename not in transfers:
                        transfers[filename] = {
                            'chunks': {},
                            'total_chunks': total_chunks,
                            'sender': addr[0],
                            'started': datetime.now()
                        }
                    
                    transfers[filename]['chunks'][chunk_idx] = base64.b64decode(chunk_data)
                    received = len(transfers[filename]['chunks'])
                    
                    print(f"[{filename}] Chunk {received}/{total_chunks} from {addr[0]}")
                    
                    if received == total_chunks:
                        # Assemble file
                        file_data = b''.join(transfers[filename]['chunks'][i] for i in range(total_chunks))
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
                        print(f"✓ Saved: {filename} ({len(file_data)} bytes)\n")
                        del transfers[filename]
        
        except Exception as e:
            print(f"Error: {e}")

class FileServerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logs
    
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.serve_dashboard()
        elif self.path == '/api/files':
            self.serve_api()
        elif self.path.startswith('/download/'):
            self.serve_file()
        else:
            self.send_error(404)
    
    def serve_dashboard(self):
        with lock:
            files = list(received_files)
        
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Received Files</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            min-height: 100vh;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{ font-size: 32px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; font-size: 14px; }}
        .status {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
            border-bottom: 1px solid #e0e0e0;
        }}
        .stat-card {{
            text-align: center;
            padding: 15px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .stat-card h3 {{ font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}
        .stat-card .value {{ font-size: 28px; font-weight: bold; color: #667eea; }}
        .files-section {{ padding: 30px; }}
        .files-section h2 {{ font-size: 18px; margin-bottom: 20px; color: #333; }}
        .empty {{ text-align: center; color: #999; padding: 40px; }}
        .file-card {{
            background: #f8f9fa;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 20px;
            transition: all 0.3s;
        }}
        .file-card:hover {{
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            transform: translateY(-2px);
        }}
        .file-icon {{
            width: 50px;
            height: 50px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            flex-shrink: 0;
        }}
        .file-info {{ flex: 1; }}
        .file-name {{ font-size: 16px; font-weight: 600; color: #333; margin-bottom: 5px; }}
        .file-meta {{ font-size: 13px; color: #666; }}
        .file-meta span {{ margin-right: 15px; }}
        .download-btn {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            text-decoration: none;
            display: inline-block;
            transition: transform 0.2s;
        }}
        .download-btn:hover {{ transform: scale(1.05); }}
        .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #00ff88; margin-right: 8px; animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📥 Received Files</h1>
            <p><span class="dot"></span>Listening on UDP port {UDP_PORT}</p>
        </div>
        
        <div class="status">
            <div class="stat-card">
                <h3>Files Received</h3>
                <div class="value">{len(files)}</div>
            </div>
            <div class="stat-card">
                <h3>Total Size</h3>
                <div class="value">{self.format_bytes(sum(f["size"] for f in files))}</div>
            </div>
            <div class="stat-card">
                <h3>Last Update</h3>
                <div class="value" style="font-size: 16px;">{files[0]["received"].split("T")[1][:8] if files else "—"}</div>
            </div>
        </div>
        
        <div class="files-section">
            <h2>📂 Your Files</h2>
            {self.render_files(files)}
        </div>
    </div>
    
    <script>
        setInterval(() => {{ location.reload(); }}, 5000); // Auto-refresh every 5 seconds
    </script>
</body>
</html>'''
        
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def render_files(self, files):
        if not files:
            return '<div class="empty">No files received yet. Waiting for transfers...</div>'
        
        html = ''
        for f in files:
            ext = Path(f['filename']).suffix.lower()
            icon = '📄' if ext in ['.txt', '.pdf', '.doc'] else '🖼️' if ext in ['.jpg', '.png', '.gif'] else '📊' if ext in ['.xls', '.csv'] else '📦'
            
            html += f'''
            <div class="file-card">
                <div class="file-icon">{icon}</div>
                <div class="file-info">
                    <div class="file-name">{f["filename"]}</div>
                    <div class="file-meta">
                        <span><strong>Size:</strong> {self.format_bytes(f["size"])}</span>
                        <span><strong>From:</strong> {f["sender"]}</span>
                        <span><strong>Time:</strong> {datetime.fromisoformat(f["received"]).strftime("%I:%M:%S %p")}</span>
                    </div>
                </div>
                <a href="/download/{f["filename"]}" class="download-btn" download>⬇ Download</a>
            </div>
            '''
        return html
    
    def serve_api(self):
        with lock:
            files = list(received_files)
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(files).encode())
    
    def serve_file(self):
        filename = unquote(self.path[10:])  # Remove '/download/'
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            self.send_header('Content-type', 'application/octet-stream')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)
    
    @staticmethod
    def format_bytes(bytes):
        if bytes == 0: return '0 B'
        k = 1024
        sizes = ['B', 'KB', 'MB', 'GB']
        i = 0
        while bytes >= k and i < len(sizes) - 1:
            bytes /= k
            i += 1
        return f'{bytes:.1f} {sizes[i]}'

if __name__ == '__main__':
    create_output_dir()
    
    # Start UDP listener in background
    udp_thread = threading.Thread(target=receive_udp, daemon=True)
    udp_thread.start()
    
    # Start HTTP server
    print(f"\n{'='*60}")
    print(f"  UNIVERSAL FILE RECEIVER")
    print(f"{'='*60}")
    print(f"\n  📡 Receiving files on UDP port: {UDP_PORT}")
    print(f"  🌐 Web interface at: http://localhost:{HTTP_PORT}")
    print(f"  📁 Files saved to: {os.path.abspath(OUTPUT_DIR)}")
    print(f"\n  👉 Open in browser: http://localhost:{HTTP_PORT}")
    print(f"  👉 Or share: http://YOUR-IP-ADDRESS:{HTTP_PORT}")
    print(f"\n{'='*60}\n")
    
    server = HTTPServer(('0.0.0.0', HTTP_PORT), FileServerHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        server.shutdown()
