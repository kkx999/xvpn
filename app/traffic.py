from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def panel_zone(tz_name="UTC"):
    try:
        return ZoneInfo(str(tz_name or "UTC"))
    except ZoneInfoNotFoundError:
        return timezone.utc


def local_datetime(now=None, tz_name="UTC"):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(panel_zone(tz_name))


def traffic_period_keys(now=None, tz_name="UTC"):
    local = local_datetime(now, tz_name)
    day = local.date().isoformat()
    return day, day[:7]


def traffic_summary(conn, user_id, now=None, tz_name="UTC"):
    day, month = traffic_period_keys(now, tz_name)
    row = conn.execute(
        """SELECT
               COALESCE(SUM(CASE WHEN day=? THEN upload_bytes ELSE 0 END),0) today_upload,
               COALESCE(SUM(CASE WHEN day=? THEN download_bytes ELSE 0 END),0) today_download,
               COALESCE(SUM(CASE WHEN substr(day,1,7)=? THEN upload_bytes ELSE 0 END),0) month_upload,
               COALESCE(SUM(CASE WHEN substr(day,1,7)=? THEN download_bytes ELSE 0 END),0) month_download,
               COALESCE(SUM(upload_bytes),0) total_upload,
               COALESCE(SUM(download_bytes),0) total_download
           FROM traffic_daily WHERE user_id=?""",
        (day, day, month, month, user_id),
    ).fetchone()
    last = conn.execute(
        """SELECT MAX(last_report_at) last_report_at, COUNT(*) device_count
           FROM traffic_device_counters WHERE user_id=?""",
        (user_id,),
    ).fetchone()
    return {
        "today_upload": int(row["today_upload"] or 0),
        "today_download": int(row["today_download"] or 0),
        "month_upload": int(row["month_upload"] or 0),
        "month_download": int(row["month_download"] or 0),
        "total_upload": int(row["total_upload"] or 0),
        "total_download": int(row["total_download"] or 0),
        "last_report_at": last["last_report_at"],
        "device_count": int(last["device_count"] or 0),
        "timezone": str(tz_name or "UTC"),
        "day": day,
        "month": month,
    }


def format_bytes(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        size = 0.0
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    if size >= 100:
        return f"{size:.0f} {units[index]}"
    if size >= 10:
        return f"{size:.1f} {units[index]}"
    return f"{size:.2f} {units[index]}"


def mask_device_id(value):
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}…{value[-4:]}"
