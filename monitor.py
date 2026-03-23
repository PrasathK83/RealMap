import json
import os
import time

STATE_FILE = os.path.join(os.path.dirname(__file__), 'device_state.json')
os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)


class ChangeMonitor:
    def __init__(self):
        self.previous_state = self._load_state()

    def detect_changes(self, current_devices):
        """Compare current scan with previous — return list of changes."""
        changes = []
        current_ips = {d['ip']: d for d in current_devices}
        prev_ips    = self.previous_state

        # New devices
        for ip, device in current_ips.items():
            if ip not in prev_ips:
                changes.append({
                    'type':      'new_device',
                    'ip':        ip,
                    'hostname':  device.get('hostname', ip),
                    'device_type': device.get('type', 'device'),
                    'message':   f"New device joined: {device.get('hostname', ip)} ({ip})",
                    'severity':  'warning' if device.get('type') == 'device' else 'info',
                    'timestamp': time.strftime('%H:%M:%S')
                })

        # Removed devices
        for ip in prev_ips:
            if ip not in current_ips:
                prev = prev_ips[ip]
                changes.append({
                    'type':     'device_left',
                    'ip':       ip,
                    'hostname': prev.get('hostname', ip),
                    'message':  f"Device went offline: {prev.get('hostname', ip)} ({ip})",
                    'severity': 'error',
                    'timestamp': time.strftime('%H:%M:%S')
                })

        # Latency spikes
        for ip, device in current_ips.items():
            if ip in prev_ips:
                prev_latency = prev_ips[ip].get('latency', 0) or 0
                curr_latency = device.get('latency', 0) or 0
                if prev_latency > 0 and curr_latency > prev_latency * 3 and curr_latency > 50:
                    changes.append({
                        'type':     'latency_spike',
                        'ip':       ip,
                        'hostname': device.get('hostname', ip),
                        'message':  f"Latency spike on {device.get('hostname', ip)}: {prev_latency}ms → {curr_latency}ms",
                        'severity': 'warning',
                        'timestamp': time.strftime('%H:%M:%S')
                    })

        # Save new state
        self.previous_state = current_ips
        self._save_state(current_ips)

        return changes

    def _save_state(self, state):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except:
            pass

    def _load_state(self):
        try:
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}
