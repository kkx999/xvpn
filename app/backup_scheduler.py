from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from .backup_manager import create_backup, prune_auto_backups
from .db import connect
from .settings_store import get_settings, set_settings
from .telegram_client import send_backup, send_message
from .version import APP_VERSION

INTERVAL_HOURS = {
    "6h": 6,
    "12h": 12,
    "daily": 24,
    "3d": 72,
    "weekly": 168,
}


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _tz(name):
    try:
        return ZoneInfo(name or "UTC")
    except ZoneInfoNotFoundError:
        return timezone.utc


def _scheduled_today(now_local, hhmm):
    try:
        hour, minute = [int(x) for x in str(hhmm).split(":", 1)]
        hour = min(23, max(0, hour)); minute = min(59, max(0, minute))
    except Exception:
        hour, minute = 4, 0
    return now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def is_due(values, now_utc=None):
    if values.get("auto_backup_enabled", "0") != "1":
        return False
    interval = values.get("backup_interval", "daily")
    hours = INTERVAL_HOURS.get(interval, 24)
    now_utc = now_utc or datetime.now(timezone.utc)
    last = _parse_iso(values.get("backup_last_run_at"))
    tz = _tz(values.get("backup_timezone", "UTC"))
    now_local = now_utc.astimezone(tz)

    # Short intervals start immediately if never run, then use elapsed time.
    if interval in {"6h", "12h"}:
        return not last or now_utc - last.astimezone(timezone.utc) >= timedelta(hours=hours)

    target = _scheduled_today(now_local, values.get("backup_time", "04:00"))
    if now_local < target:
        return False
    if not last:
        return True
    last_local = last.astimezone(tz)
    day_gap = (now_local.date() - last_local.date()).days
    required_days = {"daily": 1, "3d": 3, "weekly": 7}.get(interval, 1)
    # Same-day repeated timer calls must not create duplicates.
    return day_gap >= required_days


def _stats():
    with connect() as conn:
        return {
            "nodes": conn.execute("SELECT COUNT(*) FROM nodes WHERE status='enabled'").fetchone()[0],
            "users": conn.execute("SELECT COUNT(*) FROM users WHERE status='active'").fetchone()[0],
            "invites": conn.execute("SELECT COUNT(*) FROM invites WHERE status='active' AND use_count < max_uses").fetchone()[0],
        }


def run_scheduled_backup_once(force=False):
    values = get_settings()
    if not force and not is_due(values):
        return {"ran": False, "reason": "not_due"}
    now = datetime.now(timezone.utc)
    set_settings({"backup_last_run_at": now.isoformat(timespec="seconds")})
    try:
        path = create_backup("auto")
        try:
            keep = int(values.get("backup_keep", "7"))
        except ValueError:
            keep = 7
        prune_auto_backups(max(1, min(100, keep)))
        status = f"成功：{path.name}"
        set_settings({"backup_last_status": status})
    except Exception as exc:
        msg = f"失败：{exc}"
        set_settings({"backup_last_status": msg})
        if values.get("telegram_enabled", "0") == "1":
            try:
                send_message(f"VPN Panel 自动备份失败\n\n时间：{now.isoformat(timespec='seconds')}\n原因：{exc}")
            except Exception:
                pass
        return {"ran": True, "ok": False, "error": str(exc)}

    telegram_result = "未开启 Telegram 发送"
    if values.get("telegram_enabled", "0") == "1":
        stats = _stats()
        size_mb = path.stat().st_size / 1024 / 1024
        caption = (
            "VPN Panel 自动备份\n\n"
            f"时间：{now.isoformat(timespec='seconds')}\n"
            f"版本：{APP_VERSION}\n"
            f"启用节点：{stats['nodes']}\n"
            f"正常用户：{stats['users']}\n"
            f"可用邀请码：{stats['invites']}\n"
            f"备份大小：{size_mb:.2f} MB"
        )
        try:
            send_backup(path, caption)
            telegram_result = f"成功：{path.name}"
        except Exception as exc:
            telegram_result = f"失败：{exc}"
        set_settings({"telegram_last_status": telegram_result})
    return {"ran": True, "ok": True, "path": str(path), "telegram": telegram_result}
