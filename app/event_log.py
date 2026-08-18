from .db import connect, utcnow


MAX_EVENT_LOGS = 500


def log_event(category, level, message, app=None):
    """Best-effort bounded operational history; logging must never break core jobs."""
    category = (str(category or "system").strip().lower() or "system")[:32]
    level = (str(level or "info").strip().lower() or "info")[:16]
    message = str(message or "").strip()[:800] or "—"
    try:
        with connect(app) as conn:
            conn.execute(
                "INSERT INTO system_event_logs(category,level,message,created_at) VALUES(?,?,?,?)",
                (category, level, message, utcnow()),
            )
            # Keep the table bounded even after years of operation.
            conn.execute(
                """DELETE FROM system_event_logs
                   WHERE id NOT IN (
                       SELECT id FROM system_event_logs ORDER BY id DESC LIMIT ?
                   )""",
                (MAX_EVENT_LOGS,),
            )
            conn.commit()
        return True
    except Exception:
        return False


def list_events(limit=10, app=None):
    limit = max(1, min(100, int(limit)))
    with connect(app) as conn:
        return conn.execute(
            "SELECT id,category,level,message,created_at FROM system_event_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def clear_events(app=None):
    with connect(app) as conn:
        conn.execute("DELETE FROM system_event_logs")
        conn.commit()
