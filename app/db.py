import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from flask import current_app
from werkzeug.security import generate_password_hash

SCHEMA = r'''
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    session_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','used','revoked')),
    max_uses INTEGER NOT NULL DEFAULT 1 CHECK(max_uses>=1),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count>=0),
    used_by INTEGER,
    created_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
    invite_id INTEGER,
    created_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT,
    FOREIGN KEY(invite_id) REFERENCES invites(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL,
    country_code TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    protocol TEXT NOT NULL,
    config_enc TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'enabled' CHECK(status IN ('enabled','disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS country_orders (
    country_code TEXT PRIMARY KEY,
    sort_order INTEGER NOT NULL DEFAULT 100,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS admin_api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    FOREIGN KEY(admin_id) REFERENCES admins(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS traffic_device_counters (
    user_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    node_id INTEGER NOT NULL DEFAULT 0,
    upload_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_total_bytes>=0),
    download_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_total_bytes>=0),
    app_version TEXT NOT NULL DEFAULT '',
    last_report_at TEXT NOT NULL,
    PRIMARY KEY(user_id,device_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS traffic_daily (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes>=0),
    download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes>=0),
    report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count>=0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id,day),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS traffic_node_daily (
    user_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    node_name TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes>=0),
    download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes>=0),
    report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count>=0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id,node_id,day),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS system_event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_rate_limits (
    rate_key TEXT PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    window_started_at TEXT NOT NULL,
    blocked_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_status_sort ON nodes(status,country_code,sort_order,id);
CREATE INDEX IF NOT EXISTS idx_country_orders_sort ON country_orders(sort_order,country_code);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_traffic_daily_day ON traffic_daily(day,user_id);
CREATE INDEX IF NOT EXISTS idx_traffic_counters_report ON traffic_device_counters(last_report_at);
CREATE INDEX IF NOT EXISTS idx_traffic_node_daily_day ON traffic_node_daily(day,node_id,user_id);
CREATE INDEX IF NOT EXISTS idx_system_event_logs_created ON system_event_logs(created_at,id);
'''


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(app=None):
    app = app or current_app
    conn = sqlite3.connect(app.config['DATABASE_PATH'], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


@contextmanager
def transaction(app=None):
    conn = connect(app)
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(app):
    with connect(app) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def bootstrap_admin(app):
    import os
    password = os.environ.get('ADMIN_PASSWORD', '').strip()
    with connect(app) as conn:
        if conn.execute('SELECT 1 FROM admins LIMIT 1').fetchone():
            return
        if not password or password == 'change-me-now':
            raise RuntimeError('ADMIN_PASSWORD is required for first-run bootstrap')
        now = utcnow()
        conn.execute(
            'INSERT INTO admins(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)',
            ('admin', generate_password_hash(password, method='scrypt'), now, now),
        )
        conn.commit()
