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

CREATE TABLE IF NOT EXISTS invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','used','revoked')),
    max_uses INTEGER NOT NULL DEFAULT 1 CHECK(max_uses >= 1),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK(use_count >= 0),
    used_by INTEGER,
    created_at TEXT NOT NULL,
    used_at TEXT,
    FOREIGN KEY(used_by) REFERENCES users(id) ON DELETE SET NULL
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
    upload_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_total_bytes >= 0),
    download_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_total_bytes >= 0),
    app_version TEXT NOT NULL DEFAULT '',
    last_report_at TEXT NOT NULL,
    PRIMARY KEY(user_id, device_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS traffic_daily (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes >= 0),
    download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes >= 0),
    report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, day),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS traffic_node_daily (
    user_id INTEGER NOT NULL,
    node_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    node_name TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes >= 0),
    download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes >= 0),
    report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, node_id, day),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS system_event_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_status_sort ON nodes(status, country_code, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_country_orders_sort ON country_orders(sort_order, country_code);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_traffic_daily_day ON traffic_daily(day, user_id);
CREATE INDEX IF NOT EXISTS idx_traffic_counters_report ON traffic_device_counters(last_report_at);
CREATE INDEX IF NOT EXISTS idx_traffic_node_daily_day ON traffic_node_daily(day, node_id, user_id);
CREATE INDEX IF NOT EXISTS idx_system_event_logs_created ON system_event_logs(created_at, id);

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

'''


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(app=None):
    app = app or current_app
    conn = sqlite3.connect(app.config["DATABASE_PATH"], timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(app=None):
    conn = connect(app)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn):
    """Small additive migrations so legacy databases can be upgraded in place."""
    admin_cols = _columns(conn, "admins")
    if "updated_at" not in admin_cols:
        conn.execute("ALTER TABLE admins ADD COLUMN updated_at TEXT")
    if "session_version" not in admin_cols:
        conn.execute("ALTER TABLE admins ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1")
    user_cols = _columns(conn, "users")
    if "password_changed_at" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT")

    invite_cols = _columns(conn, "invites")
    if "max_uses" not in invite_cols:
        conn.execute("ALTER TABLE invites ADD COLUMN max_uses INTEGER NOT NULL DEFAULT 1")
    if "use_count" not in invite_cols:
        conn.execute("ALTER TABLE invites ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0")
    # Existing legacy invitations were single-use. Preserve their consumed state.
    conn.execute("UPDATE invites SET max_uses=1 WHERE max_uses IS NULL OR max_uses < 1")
    conn.execute("UPDATE invites SET use_count=1 WHERE status='used' AND use_count < 1")
    conn.execute("UPDATE invites SET use_count=0 WHERE use_count IS NULL OR use_count < 0")
    conn.execute("UPDATE invites SET status='used' WHERE status='active' AND use_count >= max_uses")

    node_cols = _columns(conn, "nodes")
    if "original_name" not in node_cols:
        conn.execute("ALTER TABLE nodes ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE nodes SET original_name=name WHERE original_name='' OR original_name IS NULL")

    # Persistent first-level country/category ordering. Existing installs keep
    # their previous country-code order on the first migration, avoiding a visual
    # reshuffle during upgrade. New countries are appended automatically.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS country_orders (
               country_code TEXT PRIMARY KEY,
               sort_order INTEGER NOT NULL DEFAULT 100,
               updated_at TEXT NOT NULL
           )"""
    )
    existing_codes = {
        row[0] for row in conn.execute("SELECT country_code FROM country_orders").fetchall()
    }
    next_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) FROM country_orders").fetchone()[0] + 10
    for row in conn.execute("SELECT DISTINCT country_code FROM nodes ORDER BY country_code").fetchall():
        code = row[0]
        if code and code not in existing_codes:
            conn.execute(
                "INSERT INTO country_orders(country_code,sort_order,updated_at) VALUES(?,?,?)",
                (code, next_order, utcnow()),
            )
            existing_codes.add(code)
            next_order += 10

    # Web-managed system settings and backup automation.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS system_settings (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )
    defaults = {
        "panel_name": "XVPN Panel",
        "panel_subtitle": "私人访问控制台",
        "token_days": "30",
        "registration_enabled": "1",
        "auto_backup_enabled": "0",
        "backup_interval": "daily",
        "backup_time": "04:00",
        "backup_keep": "7",
        "backup_timezone": "UTC",
        "panel_timezone": "UTC",
        "telegram_enabled": "0",
        "telegram_chat_id": "",
        "telegram_bot_token_enc": "",
        "backup_last_run_at": "",
        "backup_last_status": "尚未执行自动备份",
        "telegram_last_status": "尚未发送",
    }
    had_panel_timezone = conn.execute("SELECT 1 FROM system_settings WHERE key='panel_timezone'").fetchone() is not None
    for key, value in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO system_settings(key,value,updated_at) VALUES(?,?,?)",
            (key, value, utcnow()),
        )
    # Upgraded installations already used backup_timezone as their visible local timezone.
    # Adopt it once as the unified Panel timezone instead of silently reverting to UTC.
    if not had_panel_timezone:
        legacy_tz = conn.execute("SELECT value FROM system_settings WHERE key='backup_timezone'").fetchone()
        if legacy_tz and legacy_tz[0]:
            conn.execute(
                "UPDATE system_settings SET value=?,updated_at=? WHERE key='panel_timezone'",
                (legacy_tz[0], utcnow()),
            )
    # v1.0.0 removes the unused service API Key feature.
    # Drop legacy objects when upgrading so no stale API-key credentials remain.
    conn.execute("DROP INDEX IF EXISTS idx_api_keys_status")
    conn.execute("DROP TABLE IF EXISTS api_keys")
    # Lightweight API brute-force protection state.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS auth_rate_limits (
               rate_key TEXT PRIMARY KEY,
               attempts INTEGER NOT NULL DEFAULT 0,
               window_started_at TEXT NOT NULL,
               blocked_until TEXT
           )"""
    )

    # Android traffic reporting and bounded runtime event history.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traffic_device_counters (
               user_id INTEGER NOT NULL,
               device_id TEXT NOT NULL,
               session_id TEXT NOT NULL,
               node_id INTEGER NOT NULL DEFAULT 0,
               upload_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_total_bytes >= 0),
               download_total_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_total_bytes >= 0),
               app_version TEXT NOT NULL DEFAULT '',
               last_report_at TEXT NOT NULL,
               PRIMARY KEY(user_id, device_id),
               FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
           )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traffic_daily (
               user_id INTEGER NOT NULL,
               day TEXT NOT NULL,
               upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes >= 0),
               download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes >= 0),
               report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count >= 0),
               updated_at TEXT NOT NULL,
               PRIMARY KEY(user_id, day),
               FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
           )"""
    )
    # attribute client-reported deltas to the exact node/session.
    traffic_counter_cols = _columns(conn, "traffic_device_counters")
    if "node_id" not in traffic_counter_cols:
        conn.execute("ALTER TABLE traffic_device_counters ADD COLUMN node_id INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS traffic_node_daily (
               user_id INTEGER NOT NULL,
               node_id INTEGER NOT NULL,
               day TEXT NOT NULL,
               node_name TEXT NOT NULL DEFAULT '',
               country TEXT NOT NULL DEFAULT '',
               region TEXT NOT NULL DEFAULT '',
               upload_bytes INTEGER NOT NULL DEFAULT 0 CHECK(upload_bytes >= 0),
               download_bytes INTEGER NOT NULL DEFAULT 0 CHECK(download_bytes >= 0),
               report_count INTEGER NOT NULL DEFAULT 0 CHECK(report_count >= 0),
               updated_at TEXT NOT NULL,
               PRIMARY KEY(user_id, node_id, day),
               FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
           )"""
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS system_event_logs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               category TEXT NOT NULL,
               level TEXT NOT NULL DEFAULT 'info',
               message TEXT NOT NULL,
               created_at TEXT NOT NULL
           )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traffic_daily_day ON traffic_daily(day, user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traffic_counters_report ON traffic_device_counters(last_report_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traffic_node_daily_day ON traffic_node_daily(day, node_id, user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_system_event_logs_created ON system_event_logs(created_at, id)")


def init_db(app):
    with connect(app) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()


def bootstrap_admin(app):
    import os

    # Fresh installs still start with admin, but the username is editable after login.
    username = "admin"
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    with connect(app) as conn:
        exists = conn.execute("SELECT 1 FROM admins LIMIT 1").fetchone()
        if exists:
            return
        if not password or password == "change-me-now":
            raise RuntimeError("ADMIN_PASSWORD is required for first-run bootstrap")
        conn.execute(
            "INSERT INTO admins(username,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
            (username, generate_password_hash(password, method="scrypt"), utcnow(), utcnow()),
        )
        conn.commit()
