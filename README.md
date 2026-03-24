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

## Deploying to Render

This app now supports a cloud-safe mode by default on Render:

- Active LAN scanning is disabled automatically.
- Background scanner loop is disabled automatically.
- Network-heavy probe endpoints are disabled automatically.

This prevents crashes/timeouts in cloud environments that do not expose your local subnet.

### Required start command

Use the included `Procfile`:

```text
web: gunicorn --worker-class eventlet --workers 1 --bind 0.0.0.0:$PORT app:app
```

### Optional environment variables

- `REALMAP_CLOUD_MODE=1` force cloud-safe behavior.
- `REALMAP_ENABLE_SCANNING=1` re-enable LAN scan logic.
- `REALMAP_ENABLE_BACKGROUND_SCANNER=1` re-enable periodic scanner loop.
- `REALMAP_ENABLE_ACTIVE_PROBES=1` re-enable port/recon/dns/share probe routes.
- `REALMAP_DATA_DIR=/tmp/realmap` change runtime file location.
- `REALMAP_INGEST_TOKEN=<secret>` secure token required by `POST /api/ingest`.

### Cloud limitations and edge ingest

- LAN topology discovery does not work directly from Render.
- Use a local RealMap instance or scanner on your LAN and send payloads to:

```text
POST /api/ingest
```

Accepted payloads:

- Direct topology payload with `nodes` and `links`
- Wrapped outbound payload from `/api/share` where topology is in `data`

When cloud mode is enabled, active probes are blocked for private/LAN target IPs.

### Run local edge uploader (recommended)

Run this on a machine inside your LAN to keep the hosted dashboard live:

```bash
python edge_uploader.py --url https://your-app.onrender.com --token YOUR_TOKEN --interval 15
```

Environment-variable alternative:

```bash
export REALMAP_EDGE_TARGET_URL=https://your-app.onrender.com
export REALMAP_INGEST_TOKEN=YOUR_TOKEN
python edge_uploader.py
```

Windows PowerShell:

```powershell
$env:REALMAP_EDGE_TARGET_URL="https://your-app.onrender.com"
$env:REALMAP_INGEST_TOKEN="YOUR_TOKEN"
python edge_uploader.py
```

Note: Render filesystem is ephemeral unless a persistent disk is attached.

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
