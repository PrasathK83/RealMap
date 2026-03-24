import argparse
import os
import time
from typing import Any, cast

import requests

from scanner import NetworkScanner
from topology import TopologyManager
from monitor import ChangeMonitor


def calculate_network_health(devices: list[dict[str, Any]], stats: dict[str, Any]) -> int:
    if not devices:
        return 50

    score = 100
    offline_count = sum(1 for d in devices if d.get('status') == 'offline')
    warning_count = sum(1 for d in devices if d.get('status') == 'warning')
    score -= offline_count * 5
    score -= warning_count * 2

    avg_latency = stats.get('avg_latency', 0)
    if avg_latency > 100:
        score -= 20
    elif avg_latency > 50:
        score -= 10

    total = len(devices)
    online = stats.get('online', 0)
    if total > 0 and (online / total) < 0.8:
        score -= 15

    return max(0, min(100, score))


def build_payload(
    scanner: Any,
    topology: Any,
    monitor: Any,
) -> dict[str, Any]:
    devices = cast(list[dict[str, Any]], scanner.discover_devices())
    links = cast(list[dict[str, Any]], scanner.measure_links(devices))
    graph = topology.build_graph(devices, links)
    stats = cast(dict[str, Any], topology.get_stats(graph))
    changes = cast(list[dict[str, Any]], monitor.detect_changes(devices))
    health = calculate_network_health(devices, stats)

    return {
        'nodes': devices,
        'links': links,
        'stats': stats,
        'changes': changes,
        'health': health,
        'scan_mode': 'edge_ingest',
        'timestamp': time.strftime('%H:%M:%S')
    }


def post_payload(endpoint: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-RealMap-Token'] = token

    response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def normalize_url(url: str) -> str:
    cleaned = url.strip().rstrip('/')
    if not cleaned:
        raise ValueError('A base URL is required (for example: https://your-app.onrender.com).')
    if not cleaned.startswith('http://') and not cleaned.startswith('https://'):
        cleaned = 'https://' + cleaned
    return cleaned


def run_loop(base_url: str, token: str, interval: int, timeout: int, once: bool) -> None:
    scanner = NetworkScanner()
    topology = TopologyManager()
    monitor = ChangeMonitor()

    endpoint = normalize_url(base_url) + '/api/ingest'
    print('[EdgeUploader] Target:', endpoint)
    print('[EdgeUploader] Interval:', interval, 'seconds')
    print('[EdgeUploader] Token set:', 'yes' if token else 'no')

    while True:
        started = time.time()
        try:
            payload = build_payload(scanner, topology, monitor)
            result = post_payload(endpoint, token, payload, timeout)
            print(
                '[EdgeUploader] OK',
                f"nodes={len(payload.get('nodes', []))}",
                f"health={payload.get('health', 0)}",
                f"status={result.get('status', 'unknown')}"
            )
        except requests.RequestException as exc:
            print(f'[EdgeUploader] Upload failed: {exc}')
        except Exception as exc:
            print(f'[EdgeUploader] Scan failed: {exc}')

        if once:
            break

        elapsed = time.time() - started
        sleep_for = max(1, interval - elapsed)
        time.sleep(sleep_for)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Scan local LAN and push topology snapshots to a hosted RealMap instance.'
    )
    parser.add_argument(
        '--url',
        default=os.getenv('REALMAP_EDGE_TARGET_URL', ''),
        help='Hosted RealMap base URL, for example https://your-app.onrender.com'
    )
    parser.add_argument(
        '--token',
        default=os.getenv('REALMAP_INGEST_TOKEN', ''),
        help='Optional ingest token if server enforces REALMAP_INGEST_TOKEN'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=int(os.getenv('REALMAP_EDGE_INTERVAL', '15')),
        help='Seconds between scans (default: 15)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=int(os.getenv('REALMAP_EDGE_TIMEOUT', '20')),
        help='HTTP timeout in seconds (default: 20)'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run one scan/upload cycle and exit'
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.url:
        raise SystemExit('Missing target URL. Use --url or set REALMAP_EDGE_TARGET_URL.')

    run_loop(
        base_url=args.url,
        token=args.token,
        interval=max(5, args.interval),
        timeout=max(5, args.timeout),
        once=args.once
    )


if __name__ == '__main__':
    main()
