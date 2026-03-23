#!/usr/bin/env python3
"""
UDP File Receiver — listens on a port and reassembles received file chunks.
Run this on the target system (e.g., 172.21.16.83:5533) to receive files sent via UDP.
"""

import socket
import json
import base64
import os
from pathlib import Path

LISTEN_IP = '0.0.0.0'  # Listen on all interfaces
LISTEN_PORT = 5533
OUTPUT_DIR = 'received_files'

def create_output_dir():
    """Create output directory if it doesn't exist."""
    Path(OUTPUT_DIR).mkdir(exist_ok=True)

def receive_files():
    """Listen for UDP packets and reassemble files."""
    create_output_dir()
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((LISTEN_IP, LISTEN_PORT))
    
    print(f"[UDP Receiver] Listening on {LISTEN_IP}:{LISTEN_PORT}")
    print(f"[UDP Receiver] Received files will be saved to: {os.path.abspath(OUTPUT_DIR)}")
    print("[UDP Receiver] Waiting for incoming file transfers...\n")
    
    # Dictionary to store incomplete file transfers
    # Key: filename, Value: {chunks: {chunk_idx: data}, total_chunks: N}
    transfers = {}
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                
                try:
                    packet = json.loads(data.decode('utf-8'))
                except json.JSONDecodeError:
                    print(f"[UDP Receiver] Invalid JSON packet from {addr}, skipping")
                    continue
                
                packet_type = packet.get('type')
                
                if packet_type == 'file':
                    filename = packet.get('filename', 'unknown')
                    chunk_idx = packet.get('chunk', 0)
                    total_chunks = packet.get('total_chunks', 1)
                    chunk_data = packet.get('data', '')
                    
                    # Initialize transfer if new
                    if filename not in transfers:
                        transfers[filename] = {
                            'chunks': {},
                            'total_chunks': total_chunks,
                            'sender': addr
                        }
                    
                    # Store chunk
                    try:
                        transfers[filename]['chunks'][chunk_idx] = base64.b64decode(chunk_data)
                    except Exception as e:
                        print(f"[UDP Receiver] Error decoding chunk {chunk_idx} for {filename}: {e}")
                        continue
                    
                    received = len(transfers[filename]['chunks'])
                    print(f"[UDP Receiver] {filename}: chunk {chunk_idx + 1}/{total_chunks} received from {addr[0]}")
                    
                    # Check if all chunks received
                    if received == total_chunks:
                        print(f"[UDP Receiver] All chunks received for {filename}, assembling...")
                        
                        # Assemble file from chunks
                        file_data = b''
                        for i in range(total_chunks):
                            if i in transfers[filename]['chunks']:
                                file_data += transfers[filename]['chunks'][i]
                            else:
                                print(f"[UDP Receiver] Warning: missing chunk {i} for {filename}")
                        
                        # Save file
                        output_path = os.path.join(OUTPUT_DIR, filename)
                        with open(output_path, 'wb') as f:
                            f.write(file_data)
                        
                        file_size = len(file_data)
                        print(f"[UDP Receiver] ✓ File saved: {output_path} ({file_size} bytes)\n")
                        
                        # Clean up transfer record
                        del transfers[filename]
                
                elif packet_type == 'topology' or packet_type == 'alerts' or packet_type == 'stats':
                    # Handle data payloads (not file transfers)
                    print(f"[UDP Receiver] Received {packet_type} payload from {addr[0]} ({len(data)} bytes)")
                    payload_file = os.path.join(OUTPUT_DIR, f'{packet_type}_{Path(os.path.basename(__file__)).stem}.json')
                    with open(payload_file, 'w') as f:
                        json.dump(packet, f, indent=2)
                    print(f"[UDP Receiver] ✓ Payload saved: {payload_file}\n")
                
                else:
                    print(f"[UDP Receiver] Unknown packet type: {packet_type}")
            
            except Exception as e:
                print(f"[UDP Receiver] Receive error: {e}")
    
    except KeyboardInterrupt:
        print("\n[UDP Receiver] Shutting down...")
    finally:
        sock.close()

if __name__ == '__main__':
    receive_files()
