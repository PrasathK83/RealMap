import sqlite3
import json
import os
from datetime import datetime

DATA_DIR = os.getenv('REALMAP_DATA_DIR', os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, 'network_data.db')

class NetworkDatabase:
    def __init__(self):
        self.db_file = DB_FILE
        self._init_db()

    def _init_db(self):
        """Initialize database with tables."""
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        
        # Device status history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS device_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                mac TEXT,
                user TEXT,
                hostname TEXT,
                device_type TEXT,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Network alerts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT,
                device_ip TEXT,
                device_user TEXT,
                message TEXT,
                severity TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Network statistics table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS network_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_count INTEGER,
                online_count INTEGER,
                avg_latency REAL,
                network_health INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()

    def log_device_status(self, ip, mac, user, hostname, device_type, status):
        """Log device status change."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO device_status (ip, mac, user, hostname, device_type, status)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (ip, mac, user, hostname, device_type, status))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Database] Error logging device status: {e}")

    def add_alert(self, alert_type, device_ip, device_user, message, severity='info'):
        """Add network alert."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO alerts (alert_type, device_ip, device_user, message, severity)
                VALUES (?, ?, ?, ?, ?)
            ''', (alert_type, device_ip, device_user, message, severity))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"[Database] Error adding alert: {e}")
        return False

    def log_network_stats(self, device_count, online_count, avg_latency, health):
        """Log network statistics."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO network_stats (device_count, online_count, avg_latency, network_health)
                VALUES (?, ?, ?, ?)
            ''', (device_count, online_count, avg_latency, int(health)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Database] Error logging network stats: {e}")

    def get_alerts(self, limit=50):
        """Get recent alerts."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT alert_type, device_ip, device_user, message, severity, timestamp
                FROM alerts
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))
            alerts = cursor.fetchall()
            conn.close()
            return [
                {
                    'type': row[0],
                    'device_ip': row[1],
                    'device_user': row[2],
                    'message': row[3],
                    'severity': row[4],
                    'timestamp': row[5]
                }
                for row in alerts
            ]
        except Exception as e:
            print(f"[Database] Error getting alerts: {e}")
        return []

    def get_device_history(self, ip, days=7):
        """Get status history for a device."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT status, timestamp
                FROM device_status
                WHERE ip = ? AND timestamp > datetime('now', '-{} days')
                ORDER BY timestamp DESC
            '''.format(days), (ip,))
            history = cursor.fetchall()
            conn.close()
            return [
                {'status': row[0], 'timestamp': row[1]}
                for row in history
            ]
        except Exception as e:
            print(f"[Database] Error getting device history: {e}")
        return []

    def get_network_stats(self, hours=24):
        """Get network statistics."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT device_count, online_count, avg_latency, network_health, timestamp
                FROM network_stats
                WHERE timestamp > datetime('now', '-{} hours')
                ORDER BY timestamp DESC
            '''.format(hours), ())
            stats = cursor.fetchall()
            conn.close()
            return [
                {
                    'device_count': row[0],
                    'online_count': row[1],
                    'avg_latency': row[2],
                    'network_health': row[3],
                    'timestamp': row[4]
                }
                for row in stats
            ]
        except Exception as e:
            print(f"[Database] Error getting network stats: {e}")
        return []

    def get_uptime_percentage(self, ip, days=7):
        """Calculate device uptime percentage."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) as total, 
                       SUM(CASE WHEN status='online' THEN 1 ELSE 0 END) as online
                FROM device_status
                WHERE ip = ? AND timestamp > datetime('now', '-{} days')
            '''.format(days), (ip,))
            result = cursor.fetchone()
            conn.close()
            if result[0] > 0:
                return round((result[1] / result[0]) * 100, 2)
        except Exception as e:
            print(f"[Database] Error getting uptime: {e}")
        return 0

    def cleanup_old_data(self, days=30):
        """Remove data older than specified days."""
        try:
            conn = sqlite3.connect(self.db_file)
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM device_status
                WHERE timestamp < datetime('now', '-{} days')
            '''.format(days))
            cursor.execute('''
                DELETE FROM network_stats
                WHERE timestamp < datetime('now', '-{} days')
            '''.format(days))
            cursor.execute('''
                DELETE FROM alerts
                WHERE timestamp < datetime('now', '-{} days')
            '''.format(days))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Database] Error cleaning up: {e}")
