import networkx as nx
import json
import os
import time
from datetime import datetime

SNAPSHOT_FILE = os.path.join(os.path.dirname(__file__), 'snapshots.json')
HISTORY_FILE  = os.path.join(os.path.dirname(__file__), 'history.json')

os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)


class TopologyManager:
    def __init__(self):
        self.graph    = nx.Graph()
        self.snapshot = None
        self.history  = self._load_history()

    def build_graph(self, devices, links):
        """Build a NetworkX graph from devices and links."""
        G = nx.Graph()

        for device in devices:
            G.add_node(device['ip'],
                mac=device.get('mac', ''),
                hostname=device.get('hostname', device['ip']),
                device_type=device.get('type', 'device'),
                latency=device.get('latency', 0),
                os=device.get('os', 'Unknown'),
                status=device.get('status', 'online')
            )

        for link in links:
            G.add_edge(link['source'], link['target'],
                latency=link.get('latency', 0),
                bandwidth=link.get('bandwidth', 0),
                status=link.get('status', 'active')
            )

        self.graph = G
        return G

    def get_stats(self, G=None):
        """Get summary statistics of the topology."""
        if G is None:
            G = self.graph

        nodes = list(G.nodes(data=True))
        latencies = [d.get('latency', 0) for _, d in nodes if d.get('latency')]

        return {
            'total_devices': G.number_of_nodes(),
            'online':        sum(1 for _, d in nodes if d.get('status') == 'online'),
            'warnings':      sum(1 for _, d in nodes if d.get('status') == 'warning'),
            'offline':       sum(1 for _, d in nodes if d.get('status') == 'offline'),
            'links_count':   G.number_of_edges(),
            'avg_latency':   round(sum(latencies) / len(latencies), 2) if latencies else 0,
            'max_latency':   round(max(latencies), 2) if latencies else 0,
            'min_latency':   round(min(latencies), 2) if latencies else 0,
            'components':    nx.number_connected_components(G)
        }

    def save_snapshot(self, payload):
        """Save current topology snapshot."""
        self.snapshot = payload
        try:
            with open(SNAPSHOT_FILE, 'w') as f:
                json.dump(payload, f, indent=2)
            self._append_history(payload)
        except Exception as e:
            print(f"Snapshot save error: {e}")

    def get_latest_snapshot(self):
        """Load latest snapshot from disk."""
        if self.snapshot:
            return self.snapshot
        try:
            if os.path.exists(SNAPSHOT_FILE):
                with open(SNAPSHOT_FILE, 'r') as f:
                    return json.load(f)
        except:
            pass
        return None

    def _append_history(self, payload):
        """Append stats to history log."""
        entry = {
            'timestamp':     payload.get('timestamp', time.strftime('%H:%M:%S')),
            'date':          datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_devices': payload.get('stats', {}).get('total_devices', 0),
            'avg_latency':   payload.get('stats', {}).get('avg_latency', 0),
            'changes':       len(payload.get('changes', []))
        }
        self.history.append(entry)
        # Keep only last 100 entries
        self.history = self.history[-100:]
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(self.history, f, indent=2)
        except:
            pass

    def _load_history(self):
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    return json.load(f)
        except:
            pass
        return []

    def get_history(self):
        return self.history
