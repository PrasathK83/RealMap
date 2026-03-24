# RealMap

RealMap is a real-time network discovery and topology visualization tool built with Flask, Socket.IO, D3.js, and NetworkX.

It scans local network devices, tracks changes over time, and provides an interactive web dashboard with:

- Live topology graph
- Device status and latency insights
- Port scanning and service hints
- Deep recon and DNS lookup utilities
- Alerts/history and optional webhook or UDP sharing

## Features

- Automatic network device discovery
- Real-time topology updates via Socket.IO
- Device details: IP, hostname, MAC, status, OS hints
- Port scanner for selected IPs
- Deep recon endpoint with banner/SSL metadata for discovered open ports
- DNS lookup utility (forward and reverse)
- Health, stats, and history endpoints
- Webhook/UDP data sharing support

## Project Structure

```
realMAp/
  app.py
  scanner.py
  topology.py
  monitor.py
  database.py
  index.html
  requirements.txt
  start.sh

  # runtime/generated files
  network_data.db
  snapshots.json
  history.json
  device_state.json
```

## Requirements

- Python 3.10+
- Admin/root privileges may be required for low-level network scan operations on some OSes.

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

From the project root:

```bash
python app.py
```

Then open:

```text
http://localhost:5000
```

## API Overview

Core endpoints:

- `GET /` - Dashboard UI
- `GET /api/topology` - Current topology snapshot
- `POST /api/scan` - Trigger manual scan
- `GET /api/history` - Topology history
- `GET /api/alerts` - Recent alerts
- `GET /api/health` - Current network health
- `GET /api/stats` - Aggregate stats
- `GET /api/ips` - Available discovered IP list
- `GET /api/ports/<ip>` - Common-port scan on target IP

Recon and DNS:

- `GET /api/recon?target=<ip>`
- `GET /api/recon/<ip>`
- `GET /api/service-enum/<ip>/<port>`
- `GET /api/dns?target=<ip-or-hostname>`
- `GET /api/dns/<ip-or-hostname>`

Sharing:

- `POST /api/share` - Send topology/alerts/stats to webhook or UDP target

## Socket.IO Events

- `topology_update` (server -> client)
- `scan_started` (server -> client)
- `request_scan` (client -> server)
- `new_alert` (server -> client)

## Security and Usage Notice

Use scanning and recon features only on systems and networks you own or are explicitly authorized to assess.

## Troubleshooting

- If recon or DNS returns 404, ensure only one backend instance is running on port 5000.
- If UI appears stale, hard refresh the browser.
- If scan quality is low, run terminal as Administrator/root.
