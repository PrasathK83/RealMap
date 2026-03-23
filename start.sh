#!/bin/bash
echo ""
echo "============================================"
echo "  🗺️  RealMap — Network Topology Engine"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 not found. Please install Python 3.8+"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip3 install flask flask-socketio flask-cors eventlet networkx scapy python-nmap requests --break-system-packages -q

echo ""
echo "🚀 Starting RealMap server..."
echo "   Open: http://localhost:5000"
echo ""

cd "$(dirname "$0")"
python3 backend/app.py
