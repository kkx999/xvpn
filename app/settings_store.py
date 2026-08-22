from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import connect, utcnow

DEFAULTS = {
    "panel_name": "XVPN Panel",
    "panel_subtitle": "私人访问控制台",
    "admin_path": "admin",
    "token_days": "30",
    "registration_enabled": "1",
    # Backup / Telegram defaults
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
    # Android App update control. APK files stay in GitHub Releases; Panel only controls metadata/policy.
    "app_update_repository": "kkx999/XVPN-Android",
    "app_update_enabled": "1",
    "app_update_force": "0",
    "app_update_min_version_code": "0",
    "app_update_last_checked_at": "",
    "app_update_last_status": "尚未同步 Android Release",
    "app_update_last_snapshot_json": "",
    "app_update_last_stale": "0",
    "app_update_last_warning": "",
    "app_update_history_checked_at": "",
    "app_update_release_history_json": "",
    "app_update_history_stale": "0",
    "app_update_history_warning": "",
}


def get_settings(app=None):
    values = dict(DEFAULTS)
    with connect(app) as conn:
        rows = conn.execute("SELECT key,value FROM system_settings").fetchall()
    for row in rows:
        values[row["key"]] = row["value"]
    return values


def set_settings(values, app=None):
    now = utcnow()
    with connect(app) as conn:
        for key, value in values.items():
            conn.execute(
                """INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(value), now),
            )
        conn.commit()


def apply_settings(app):
    values = get_settings(app)
    app.config["PANEL_NAME"] = values.get("panel_name") or DEFAULTS["panel_name"]
    app.config["PANEL_SUBTITLE"] = values.get("panel_subtitle") or DEFAULTS["panel_subtitle"]
    try:
        app.config["TOKEN_DAYS"] = max(1, min(3650, int(values.get("token_days", "30"))))
    except (TypeError, ValueError):
        app.config["TOKEN_DAYS"] = 30
    app.config["REGISTRATION_ENABLED"] = values.get("registration_enabled", "1") == "1"
    tz_name = values.get("panel_timezone") or values.get("backup_timezone") or "UTC"
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "UTC"
    app.config["PANEL_TIMEZONE"] = tz_name
    return values


def initialize_from_env(app):
    with connect(app) as conn:
        marker = conn.execute("SELECT value FROM system_settings WHERE key='initialized_from_env'").fetchone()
        if marker:
            # Fresh v1.x databases may gain new settings over time; defaults are additive only.
            now = utcnow()
            for key, value in DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO system_settings(key,value,updated_at) VALUES(?,?,?)",
                    (key, value, now),
                )
            conn.commit()
            return
        import os
        values = dict(DEFAULTS)
        values.update({
            "panel_name": os.environ.get("PANEL_NAME", DEFAULTS["panel_name"]),
            "panel_subtitle": os.environ.get("PANEL_SUBTITLE", DEFAULTS["panel_subtitle"]),
            "token_days": os.environ.get("TOKEN_DAYS", DEFAULTS["token_days"]),
            "registration_enabled": "1",
            "app_update_repository": os.environ.get("XVPN_ANDROID_REPOSITORY", DEFAULTS["app_update_repository"]).strip() or DEFAULTS["app_update_repository"],
            "initialized_from_env": "1",
        })
        now = utcnow()
        for key, value in values.items():
            conn.execute(
                """INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, str(value), now),
            )
        conn.commit()
