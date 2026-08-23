import base64
import hashlib
import json
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import parse_qs, unquote, urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .crypto import decrypt_text, encrypt_text
from .db import connect, transaction, utcnow
from .backup_manager import create_backup, delete_backup, get_backup_path, list_backups, restore_backup
from .settings_store import apply_settings, get_settings, set_settings
from .telegram_client import test_connection as telegram_test_connection
from .version import APP_VERSION
from .event_log import clear_events, list_events, log_event
from .traffic import format_bytes, mask_device_id, traffic_period_keys, traffic_summary
from .app_updates import CACHE_SECONDS, CHECK_INTERVAL_SECONDS, get_app_release, get_release_history

admin_bp = Blueprint("admin", __name__, template_folder="templates", static_folder="static")

COUNTRIES = [
    ("HK", "香港"), ("JP", "日本"), ("SG", "新加坡"), ("US", "美国"), ("CA", "加拿大"),
    ("KR", "韩国"), ("TW", "台湾"), ("MO", "澳门"), ("CN", "中国大陆"), ("MY", "马来西亚"),
    ("TH", "泰国"), ("VN", "越南"), ("PH", "菲律宾"), ("ID", "印度尼西亚"), ("IN", "印度"),
    ("AU", "澳大利亚"), ("NZ", "新西兰"), ("GB", "英国"), ("DE", "德国"), ("FR", "法国"),
    ("NL", "荷兰"), ("FI", "芬兰"), ("SE", "瑞典"), ("NO", "挪威"), ("DK", "丹麦"),
    ("CH", "瑞士"), ("AT", "奥地利"), ("PL", "波兰"), ("ES", "西班牙"), ("IT", "意大利"),
    ("IE", "爱尔兰"), ("BE", "比利时"), ("CZ", "捷克"), ("RO", "罗马尼亚"), ("UA", "乌克兰"),
    ("RU", "俄罗斯"), ("TR", "土耳其"), ("AE", "阿联酋"), ("IL", "以色列"), ("SA", "沙特阿拉伯"),
    ("BR", "巴西"), ("MX", "墨西哥"), ("AR", "阿根廷"), ("CL", "智利"), ("ZA", "南非"),
    ("ZZ", "其他"),
]
COUNTRY_MAP = dict(COUNTRIES)


def country_flag(code: str):
    code = (code or "").upper()
    if len(code) != 2 or not code.isalpha() or code == "ZZ":
        return "🌐"
    return "".join(chr(127397 + ord(ch)) for ch in code)


def _country_from_form():
    code = request.form.get("country_code", "").strip().upper()
    if code not in COUNTRY_MAP:
        code = "ZZ"
    return code, COUNTRY_MAP[code]


def _ensure_country_order(conn, code: str):
    """Append a newly used country to the first-level country ordering."""
    if not code:
        return
    exists = conn.execute(
        "SELECT 1 FROM country_orders WHERE country_code=?", (code,)
    ).fetchone()
    if exists:
        return
    next_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order),0)+10 FROM country_orders"
    ).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO country_orders(country_code,sort_order,updated_at) VALUES(?,?,?)",
        (code, int(next_order or 10), utcnow()),
    )


def _active_country_orders(conn):
    """Return only countries that currently have nodes; empty categories disappear."""
    rows = conn.execute(
        """SELECT n.country_code, MAX(n.country) country, COUNT(*) node_count,
                  SUM(CASE WHEN n.status='enabled' THEN 1 ELSE 0 END) enabled_count,
                  COALESCE(co.sort_order, 999999) country_sort_order
           FROM nodes n
           LEFT JOIN country_orders co ON co.country_code=n.country_code
           GROUP BY n.country_code
           ORDER BY country_sort_order, n.country_code"""
    ).fetchall()
    return [dict(row) for row in rows]


def _client_ip():
    return request.remote_addr or ""


def _admin_login_rate_key():
    raw = f"admin-web-login:{_client_ip() or 'unknown'}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _admin_login_rate_check(max_attempts=10, window_seconds=900, block_seconds=900):
    key = _admin_login_rate_key()
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth_rate_limits WHERE rate_key=?", (key,)).fetchone()
        if not row:
            return 0
        if row["blocked_until"]:
            blocked = datetime.fromisoformat(row["blocked_until"])
            if blocked > now:
                return max(1, int((blocked - now).total_seconds()))
        started = datetime.fromisoformat(row["window_started_at"])
        if (now - started).total_seconds() > window_seconds:
            conn.execute("DELETE FROM auth_rate_limits WHERE rate_key=?", (key,))
            conn.commit()
            return 0
        if int(row["attempts"]) >= max_attempts:
            blocked = now + timedelta(seconds=block_seconds)
            conn.execute(
                "UPDATE auth_rate_limits SET blocked_until=? WHERE rate_key=?",
                (blocked.isoformat(timespec="seconds"), key),
            )
            conn.commit()
            return block_seconds
    return 0


def _admin_login_rate_fail(max_attempts=10, window_seconds=900, block_seconds=900):
    key = _admin_login_rate_key()
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth_rate_limits WHERE rate_key=?", (key,)).fetchone()
        if not row or (now - datetime.fromisoformat(row["window_started_at"])).total_seconds() > window_seconds:
            attempts = 1
            conn.execute(
                "INSERT OR REPLACE INTO auth_rate_limits(rate_key,attempts,window_started_at,blocked_until) VALUES(?,?,?,NULL)",
                (key, attempts, now.isoformat(timespec="seconds")),
            )
        else:
            attempts = int(row["attempts"]) + 1
            blocked_until = row["blocked_until"]
            if attempts >= max_attempts:
                blocked_until = (now + timedelta(seconds=block_seconds)).isoformat(timespec="seconds")
            conn.execute(
                "UPDATE auth_rate_limits SET attempts=?,blocked_until=? WHERE rate_key=?",
                (attempts, blocked_until, key),
            )
        conn.commit()
    return attempts >= max_attempts


def _admin_login_rate_reset():
    with connect() as conn:
        conn.execute("DELETE FROM auth_rate_limits WHERE rate_key=?", (_admin_login_rate_key(),))
        conn.commit()


def _page_arg(name="page"):
    try:
        return max(1, int(request.args.get(name, "1")))
    except (TypeError, ValueError):
        return 1


def _filtered_redirect(endpoint, allowed_status):
    query = request.form.get("return_q", "").strip()[:64]
    status = request.form.get("return_status", "all").strip().lower()
    if status not in allowed_status:
        status = "all"
    try:
        page = max(1, int(request.form.get("return_page", "1")))
    except (TypeError, ValueError):
        page = 1
    args = {"page": page}
    if query:
        args["q"] = query
    if status != "all":
        args["status"] = status
    return redirect(url_for(endpoint, **args))


def _users_redirect():
    return _filtered_redirect("admin.users", {"all", "active", "disabled"})


def _invites_redirect():
    return _filtered_redirect("admin.invites", {"all", "active", "used", "revoked"})


def _settings_backup_redirect():
    try:
        page = max(1, int(request.form.get("backup_page", "1")))
    except (TypeError, ValueError):
        page = 1
    return redirect(url_for("admin.settings", backup_page=page) + "#backup-records")


def _panel_time(value, short=False):
    if not value or value == "—":
        return "—"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            zone = ZoneInfo(current_app.config.get("PANEL_TIMEZONE", "UTC"))
        except ZoneInfoNotFoundError:
            zone = timezone.utc
        return dt.astimezone(zone).strftime("%Y-%m-%d %H:%M" if short else "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value).replace("T", " ")[:16 if short else 19]


@admin_bp.before_request
def admin_ip_guard():
    allowed = current_app.config.get("ADMIN_ALLOWED_IPS", set())
    if allowed and _client_ip() not in allowed:
        return "Not Found", 404


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        admin_id = session.get("admin_id")
        if not admin_id:
            return redirect(url_for("admin.login"))
        with connect() as conn:
            admin = conn.execute("SELECT id,username,session_version FROM admins WHERE id=?", (admin_id,)).fetchone()
        if not admin or int(session.get("admin_session_version", 0)) != int(admin["session_version"]):
            session.clear()
            return redirect(url_for("admin.login"))
        session["admin_username"] = admin["username"]
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf():
    token = request.form.get("csrf_token", "")
    return bool(token and secrets.compare_digest(token, session.get("csrf_token", "")))


@admin_bp.app_context_processor
def inject_globals():
    return {
        "csrf_token": csrf_token,
        "panel_name": current_app.config["PANEL_NAME"],
        "panel_subtitle": current_app.config["PANEL_SUBTITLE"],
        "countries": COUNTRIES,
        "country_map": COUNTRY_MAP,
        "country_flag": country_flag,
        "panel_version": APP_VERSION,
        "format_bytes": format_bytes,
        "mask_device_id": mask_device_id,
        "panel_time": _panel_time,
        "panel_timezone": current_app.config.get("PANEL_TIMEZONE", "UTC"),
        "one_time_secret": session.pop("one_time_secret", None),
    }


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        if not require_csrf():
            return "CSRF validation failed", 400
        retry_after = _admin_login_rate_check()
        if retry_after:
            minutes = max(1, (retry_after + 59) // 60)
            flash(f"登录失败次数过多，请约 {minutes} 分钟后再试", "error")
            return render_template("login.html"), 429
        with connect() as conn:
            admin = conn.execute(
                "SELECT * FROM admins WHERE username=? COLLATE NOCASE",
                (request.form.get("username", "").strip(),),
            ).fetchone()
        if admin and check_password_hash(admin["password_hash"], request.form.get("password", "")):
            _admin_login_rate_reset()
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["admin_session_version"] = int(admin["session_version"])
            csrf_token()
            return redirect(url_for("admin.dashboard"))
        blocked = _admin_login_rate_fail()
        if blocked:
            flash("登录失败已达到 10 次，此 IP 暂停登录 15 分钟", "error")
        else:
            flash("用户名或密码错误", "error")
    return render_template("login.html")


@admin_bp.post("/logout")
@admin_required
def logout():
    if not require_csrf():
        return "CSRF validation failed", 400
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.get("/")
@admin_required
def dashboard():
    tz_name = current_app.config.get("PANEL_TIMEZONE", "UTC")
    today, _ = traffic_period_keys(tz_name=tz_name)
    with connect() as conn:
        stats = {
            "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "active_users": conn.execute("SELECT COUNT(*) c FROM users WHERE status='active'").fetchone()["c"],
            "nodes": conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"],
            "enabled_nodes": conn.execute("SELECT COUNT(*) c FROM nodes WHERE status='enabled'").fetchone()["c"],
            "invites": conn.execute(
                "SELECT COUNT(*) c FROM invites WHERE status='active' AND use_count < max_uses"
            ).fetchone()["c"],
            "invite_slots": conn.execute(
                "SELECT COALESCE(SUM(max_uses-use_count),0) c FROM invites WHERE status='active' AND use_count < max_uses"
            ).fetchone()["c"],
        }
        traffic_row = conn.execute(
            """SELECT COALESCE(SUM(upload_bytes),0) upload_bytes,
                      COALESCE(SUM(download_bytes),0) download_bytes,
                      COUNT(CASE WHEN upload_bytes + download_bytes > 0 THEN 1 END) active_users
               FROM traffic_daily WHERE day=?""",
            (today,),
        ).fetchone()
        active_nodes = int(conn.execute(
            """SELECT COUNT(DISTINCT node_id) FROM traffic_node_daily
               WHERE day=? AND upload_bytes + download_bytes > 0""",
            (today,),
        ).fetchone()[0])
        user_rows = conn.execute(
            """SELECT u.id user_id,u.username,t.upload_bytes,t.download_bytes,
                      (t.upload_bytes+t.download_bytes) total_bytes
               FROM traffic_daily t JOIN users u ON u.id=t.user_id
               WHERE t.day=? AND (t.upload_bytes+t.download_bytes)>0
               ORDER BY total_bytes DESC,u.id ASC LIMIT 5""",
            (today,),
        ).fetchall()
        node_rows = conn.execute(
            """SELECT t.node_id,
                      COALESCE(n.name,MAX(t.node_name)) node_name,
                      COALESCE(n.country,MAX(t.country)) country,
                      COALESCE(n.region,MAX(t.region)) region,
                      SUM(t.upload_bytes) upload_bytes,
                      SUM(t.download_bytes) download_bytes,
                      SUM(t.upload_bytes+t.download_bytes) total_bytes
               FROM traffic_node_daily t
               LEFT JOIN nodes n ON n.id=t.node_id
               WHERE t.day=?
               GROUP BY t.node_id,n.name,n.country,n.region
               HAVING SUM(t.upload_bytes+t.download_bytes)>0
               ORDER BY total_bytes DESC,t.node_id ASC LIMIT 5""",
            (today,),
        ).fetchall()

    def rank_items(rows, kind):
        max_total = max((int(row["total_bytes"] or 0) for row in rows), default=0)
        items = []
        for row in rows:
            total = int(row["total_bytes"] or 0)
            item = {
                "upload_bytes": int(row["upload_bytes"] or 0),
                "download_bytes": int(row["download_bytes"] or 0),
                "total_bytes": total,
                "percent": max(5.0, round((total / max_total) * 100, 1)) if max_total else 0,
            }
            if kind == "user":
                item.update({"id": int(row["user_id"]), "label": row["username"], "meta": "用户"})
            else:
                location = " · ".join(x for x in (row["country"], row["region"]) if x)
                item.update({"id": int(row["node_id"]), "label": row["node_name"] or f"节点 {row['node_id']}", "meta": location or "节点"})
            items.append(item)
        return items

    traffic = {
        "day": today,
        "timezone": tz_name,
        "upload_bytes": int(traffic_row["upload_bytes"] or 0),
        "download_bytes": int(traffic_row["download_bytes"] or 0),
        "active_users": int(traffic_row["active_users"] or 0),
        "active_nodes": active_nodes,
        "user_rank": rank_items(user_rows, "user"),
        "node_rank": rank_items(node_rows, "node"),
    }
    return render_template("dashboard.html", stats=stats, traffic=traffic)


def detect_protocol_details(raw: str):
    """Return compact, display-only protocol traits without changing stored config."""
    raw = (raw or "").strip()
    protocol = detect_protocol(raw)
    details = [protocol.upper()]

    obj = _json_config(raw)
    if obj:
        tls = obj.get("tls") if isinstance(obj.get("tls"), dict) else {}
        if isinstance(tls, dict) and tls.get("enabled"):
            reality = tls.get("reality") if isinstance(tls.get("reality"), dict) else {}
            details.append("REALITY" if reality.get("enabled") else "TLS")
        flow = str(obj.get("flow") or "").lower()
        if "vision" in flow:
            details.append("Vision")
        transport = obj.get("transport") if isinstance(obj.get("transport"), dict) else {}
        ttype = str(transport.get("type") or obj.get("network") or "").strip()
        if ttype:
            details.append(ttype.upper() if len(ttype) <= 4 else ttype)
        return list(dict.fromkeys(details))

    if "://" in raw:
        parsed = urlsplit(raw)
        q = {k.lower(): (v[-1] if v else "") for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        security = (q.get("security") or "").lower()
        if security == "reality":
            details.append("REALITY")
        elif security in {"tls", "xtls"}:
            details.append(security.upper())
        flow = (q.get("flow") or "").lower()
        if "vision" in flow:
            details.append("Vision")
        transport = (q.get("type") or q.get("network") or "").strip()
        if transport and transport.lower() not in {"none", ""}:
            details.append(transport.upper() if len(transport) <= 4 else transport)
        if protocol == "hysteria2" and (q.get("obfs") or ""):
            details.append("OBFS")
    return list(dict.fromkeys(details))


def _node_view(row):
    item = dict(row)
    try:
        raw = decrypt_text(current_app, row["config_enc"])
        item["protocol_details"] = detect_protocol_details(raw)
    except Exception:
        item["protocol_details"] = [str(row["protocol"] or "custom").upper()]
    return item


@admin_bp.get("/nodes")
@admin_required
def nodes():
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"]
        rows = conn.execute("SELECT * FROM nodes ORDER BY id DESC LIMIT 3").fetchall()
    recent_nodes = [_node_view(row) for row in rows]
    return render_template("nodes.html", nodes=recent_nodes, total_nodes=total)


@admin_bp.get("/nodes/overview")
@admin_required
def node_overview():
    with connect() as conn:
        rows = conn.execute(
            """SELECT n.* FROM nodes n
               LEFT JOIN country_orders co ON co.country_code=n.country_code
               ORDER BY COALESCE(co.sort_order, 999999), n.country_code, n.sort_order, n.id"""
        ).fetchall()
        country_orders = _active_country_orders(conn)
    nodes_all = [_node_view(row) for row in rows]
    enabled_count = sum(1 for n in nodes_all if n["status"] == "enabled")
    country_count = len(country_orders)
    protocols = sorted({str(n["protocol"] or "custom") for n in nodes_all})
    return render_template(
        "node_overview.html",
        nodes=nodes_all,
        enabled_count=enabled_count,
        country_count=country_count,
        protocols=protocols,
        country_orders=country_orders,
    )


@admin_bp.post("/nodes/countries/order")
@admin_required
def country_order_update():
    if not require_csrf():
        return "CSRF validation failed", 400
    requested = [code.strip().upper() for code in request.form.getlist("country_code") if code.strip()]
    if len(requested) != len(set(requested)):
        flash("国家分类排序数据重复，请刷新后重试", "error")
        return redirect(url_for("admin.node_overview"))
    with transaction() as conn:
        current_rows = _active_country_orders(conn)
        current_codes = [row["country_code"] for row in current_rows]
        current = set(current_codes)
        submitted = [code for code in requested if code in current]
        # If a concurrent import introduced a new country, do not lose it: append it
        # using its current persisted order.
        missing = [code for code in current_codes if code not in set(submitted)]
        final_order = submitted + missing
        for idx, code in enumerate(final_order, start=1):
            _ensure_country_order(conn, code)
            conn.execute(
                "UPDATE country_orders SET sort_order=?,updated_at=? WHERE country_code=?",
                (idx * 10, utcnow(), code),
            )
    flash("国家分类排序已保存，App 节点分类顺序会同步更新", "success")
    return redirect(url_for("admin.node_overview"))


def _decode_b64_json(value: str):
    try:
        value = value.strip()
        value += "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(value.encode()).decode("utf-8")
        return json.loads(decoded)
    except Exception:
        try:
            decoded = base64.b64decode(value.encode()).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            return None


def _json_config(raw: str):
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def detect_protocol(raw: str):
    raw = raw.strip()
    obj = _json_config(raw)
    if obj:
        value = str(obj.get("type") or obj.get("protocol") or "").strip().lower()
        if value:
            return {"hy2": "hysteria2", "socks5": "socks"}.get(value, value)
    scheme = urlsplit(raw).scheme.lower() if "://" in raw else ""
    aliases = {"hy2": "hysteria2", "socks5": "socks", "wg": "wireguard"}
    return aliases.get(scheme, scheme or "custom")


def detect_original_name(raw: str, fallback: str = "未命名节点"):
    raw = raw.strip()
    obj = _json_config(raw)
    if obj:
        candidate = str(obj.get("tag") or obj.get("name") or "").strip()
        return candidate or fallback

    parsed = urlsplit(raw) if "://" in raw else None
    scheme = parsed.scheme.lower() if parsed else ""
    if scheme == "vmess":
        payload = raw.split("://", 1)[1].split("#", 1)[0]
        vmess = _decode_b64_json(payload)
        if vmess:
            candidate = str(vmess.get("ps") or vmess.get("remarks") or "").strip()
            if candidate:
                return candidate

    if "#" in raw:
        frag = raw.rsplit("#", 1)[1]
        candidate = unquote(frag).strip()
        if candidate:
            return candidate
    return fallback


def _to_int(value, default=100):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _node_return_url():
    return url_for("admin.node_overview") if request.form.get("return_to") == "overview" else url_for("admin.nodes")


@admin_bp.post("/nodes/add")
@admin_required
def node_add():
    if not require_csrf():
        return "CSRF validation failed", 400
    raw = request.form.get("config", "").strip()
    if not raw:
        flash("节点配置不能为空", "error")
        return redirect(url_for("admin.nodes"))
    code, country = _country_from_form()
    original_name = detect_original_name(raw)
    display_name = request.form.get("name", "").strip() or original_name
    protocol = detect_protocol(raw)
    now = utcnow()
    with connect() as conn:
        _ensure_country_order(conn, code)
        conn.execute(
            """INSERT INTO nodes(name,original_name,country,country_code,region,protocol,config_enc,sort_order,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                display_name,
                original_name,
                country,
                code,
                "",
                protocol,
                encrypt_text(current_app, raw),
                _to_int(request.form.get("sort_order"), 100),
                "enabled",
                now,
                now,
            ),
        )
        conn.commit()
    flash(f"节点已添加：{display_name}（{protocol.upper()} / {country}）", "success")
    return redirect(url_for("admin.nodes"))


@admin_bp.post("/nodes/batch")
@admin_required
def node_batch():
    if not require_csrf():
        return "CSRF validation failed", 400
    lines = [x.strip() for x in request.form.get("configs", "").splitlines() if x.strip()]
    if not lines:
        flash("没有可解析的节点", "error")
        return redirect(url_for("admin.nodes"))

    code, country = _country_from_form()
    naming_mode = request.form.get("naming_mode", "original")
    prefix = request.form.get("name_prefix", "").strip()
    start_number = max(0, _to_int(request.form.get("start_number"), 1))
    sort_base = _to_int(request.form.get("sort_order"), 100)

    if naming_mode == "sequence" and not prefix:
        flash("使用自动编号时，请填写统一名称，例如“香港”", "error")
        return redirect(url_for("admin.nodes"))

    preview = []
    for idx, raw in enumerate(lines):
        original_name = detect_original_name(raw, f"节点 {idx + 1:02d}")
        if naming_mode == "sequence":
            display_name = f"{prefix}{start_number + idx:02d}"
        else:
            display_name = original_name
        preview.append(
            {
                "config": raw,
                "original_name": original_name,
                "display_name": display_name,
                "protocol": detect_protocol(raw),
                "protocol_details": detect_protocol_details(raw),
                "sort_order": sort_base + idx,
            }
        )

    return render_template(
        "node_batch_preview.html",
        preview=preview,
        country=country,
        country_code=code,
    )


@admin_bp.post("/nodes/batch/confirm")
@admin_required
def node_batch_confirm():
    if not require_csrf():
        return "CSRF validation failed", 400
    configs = request.form.getlist("config")
    names = request.form.getlist("display_name")
    sort_orders = request.form.getlist("sort_order")
    code, country = _country_from_form()

    if not configs or len(configs) != len(names):
        flash("批量导入数据不完整，请重新解析", "error")
        return redirect(url_for("admin.nodes"))

    now = utcnow()
    with transaction() as conn:
        _ensure_country_order(conn, code)
        for idx, raw in enumerate(configs):
            raw = raw.strip()
            if not raw:
                continue
            original_name = detect_original_name(raw, f"节点 {idx + 1:02d}")
            display_name = names[idx].strip() or original_name
            sort_order = _to_int(sort_orders[idx] if idx < len(sort_orders) else None, 100 + idx)
            conn.execute(
                """INSERT INTO nodes(name,original_name,country,country_code,region,protocol,config_enc,sort_order,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    display_name,
                    original_name,
                    country,
                    code,
                    "",
                    detect_protocol(raw),
                    encrypt_text(current_app, raw),
                    sort_order,
                    "enabled",
                    now,
                    now,
                ),
            )
    flash(f"已导入 {len(configs)} 个节点；App 名称与原节点名称分别保存", "success")
    return redirect(url_for("admin.nodes"))


@admin_bp.get("/nodes/<int:node_id>/edit")
@admin_required
def node_edit_page(node_id):
    with connect() as conn:
        node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        return "Not Found", 404
    node = dict(node)
    node["config"] = decrypt_text(current_app, node["config_enc"])
    return render_template("node_edit.html", node=node, return_to=request.args.get("return_to", ""))


@admin_bp.post("/nodes/<int:node_id>/edit")
@admin_required
def node_edit(node_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    raw = request.form.get("config", "").strip()
    if not raw:
        flash("节点配置不能为空", "error")
        return redirect(url_for("admin.node_edit_page", node_id=node_id))
    code, country = _country_from_form()
    with connect() as conn:
        current = conn.execute("SELECT original_name FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not current:
            return "Not Found", 404
        original_name = detect_original_name(raw, current["original_name"] or "未命名节点")
        display_name = request.form.get("name", "").strip() or original_name
        _ensure_country_order(conn, code)
        conn.execute(
            """UPDATE nodes SET name=?,original_name=?,country=?,country_code=?,protocol=?,config_enc=?,sort_order=?,updated_at=? WHERE id=?""",
            (
                display_name,
                original_name,
                country,
                code,
                detect_protocol(raw),
                encrypt_text(current_app, raw),
                _to_int(request.form.get("sort_order"), 100),
                utcnow(),
                node_id,
            ),
        )
        conn.commit()
    flash("节点已更新；国家代码和协议均由 Panel 自动维护", "success")
    return redirect(_node_return_url())


@admin_bp.post("/nodes/<int:node_id>/toggle")
@admin_required
def node_toggle(node_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with connect() as conn:
        node = conn.execute("SELECT status FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            return "Not Found", 404
        new_status = "disabled" if node["status"] == "enabled" else "enabled"
        conn.execute("UPDATE nodes SET status=?,updated_at=? WHERE id=?", (new_status, utcnow(), node_id))
        conn.commit()
    return redirect(_node_return_url())


@admin_bp.post("/nodes/<int:node_id>/delete")
@admin_required
def node_delete(node_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with connect() as conn:
        conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        conn.commit()
    flash("节点已删除", "success")
    return redirect(_node_return_url())


@admin_bp.get("/users")
@admin_required
def users():
    page = _page_arg()
    per_page = 20
    query = request.args.get("q", "").strip()[:64]
    status_filter = request.args.get("status", "all").strip().lower()
    if status_filter not in {"all", "active", "disabled"}:
        status_filter = "all"
    today, month = traffic_period_keys(tz_name=current_app.config.get("PANEL_TIMEZONE", "UTC"))
    where = []
    params = []
    if query:
        where.append("users.username LIKE ? COLLATE NOCASE")
        params.append(f"%{query}%")
    if status_filter != "all":
        where.append("users.status=?")
        params.append(status_filter)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM users{where_sql}", params).fetchone()[0])
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = conn.execute(
            f"""WITH traffic AS (
                   SELECT user_id,
                          SUM(CASE WHEN day=? THEN upload_bytes+download_bytes ELSE 0 END) today_bytes,
                          SUM(CASE WHEN substr(day,1,7)=? THEN upload_bytes+download_bytes ELSE 0 END) month_bytes,
                          SUM(upload_bytes+download_bytes) total_bytes
                   FROM traffic_daily GROUP BY user_id
               ), reports AS (
                   SELECT user_id, MAX(last_report_at) last_report_at, COUNT(*) device_count
                   FROM traffic_device_counters GROUP BY user_id
               )
               SELECT users.*, invites.code invite_code,
                      COALESCE(traffic.today_bytes,0) today_bytes,
                      COALESCE(traffic.month_bytes,0) month_bytes,
                      COALESCE(traffic.total_bytes,0) total_bytes,
                      reports.last_report_at, COALESCE(reports.device_count,0) device_count
               FROM users
               LEFT JOIN invites ON invites.id=users.invite_id
               LEFT JOIN traffic ON traffic.user_id=users.id
               LEFT JOIN reports ON reports.user_id=users.id
               {where_sql}
               ORDER BY users.id DESC LIMIT ? OFFSET ?""",
            [today, month, *params, per_page, (page - 1) * per_page],
        ).fetchall()
    return render_template(
        "users.html", users=rows, total_users=total, page=page, pages=pages,
        query=query, status_filter=status_filter, per_page=per_page,
    )


@admin_bp.get("/users/<int:user_id>/traffic")
@admin_required
def user_traffic(user_id):
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 30
    with connect() as conn:
        user = conn.execute("SELECT id,username,status,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        summary = traffic_summary(conn, user_id, tz_name=current_app.config.get("PANEL_TIMEZONE", "UTC"))
        total_days = int(conn.execute("SELECT COUNT(*) FROM traffic_daily WHERE user_id=?", (user_id,)).fetchone()[0])
        pages = max(1, (total_days + per_page - 1) // per_page)
        page = min(page, pages)
        daily = conn.execute(
            """SELECT day,upload_bytes,download_bytes,report_count,updated_at
               FROM traffic_daily WHERE user_id=? ORDER BY day DESC LIMIT ? OFFSET ?""",
            (user_id, per_page, (page - 1) * per_page),
        ).fetchall()
        devices = conn.execute(
            """SELECT c.device_id,c.session_id,c.node_id,c.upload_total_bytes,c.download_total_bytes,c.app_version,c.last_report_at,
                      n.name node_name
               FROM traffic_device_counters c LEFT JOIN nodes n ON n.id=c.node_id WHERE c.user_id=?
               ORDER BY last_report_at DESC LIMIT 20""",
            (user_id,),
        ).fetchall()
    return render_template(
        "user_traffic.html", user=user, summary=summary, daily=daily, devices=devices,
        page=page, pages=pages, total_days=total_days,
    )


@admin_bp.post("/users/<int:user_id>/toggle")
@admin_required
def user_toggle(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with connect() as conn:
        user = conn.execute("SELECT status FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        status = "disabled" if user["status"] == "active" else "active"
        conn.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
        if status == "disabled":
            conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user_id,))
        conn.commit()
    flash("账户已停用并撤销登录状态" if status == "disabled" else "账户已恢复", "success")
    return _users_redirect()


@admin_bp.post("/users/<int:user_id>/logout-all")
@admin_required
def user_logout_all(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with transaction() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        count = int(conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=?", (user_id,)).fetchone()[0])
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user_id,))
    flash(f"{user['username']} 的登录状态已全部撤销（{count} 个 Token）", "success")
    return _users_redirect()


@admin_bp.post("/users/<int:user_id>/traffic/reset")
@admin_required
def user_traffic_reset(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with transaction() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        conn.execute("DELETE FROM traffic_daily WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM traffic_node_daily WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM traffic_session_counters WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM traffic_device_counters WHERE user_id=?", (user_id,))
    flash(f"{user['username']} 的流量统计已重置；下一次 Android 上报会从当前会话累计值重新同步", "success")
    return _users_redirect()


def make_password(length=14):
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


@admin_bp.post("/users/<int:user_id>/password")
@admin_required
def user_password(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("新密码至少 8 位", "error")
        return _users_redirect()
    with connect() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        conn.execute(
            "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
            (generate_password_hash(password, method="scrypt"), utcnow(), user_id),
        )
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user_id,))
        conn.commit()
    flash(f"{user['username']} 的密码已修改，所有登录状态已撤销", "success")
    return _users_redirect()


@admin_bp.post("/users/<int:user_id>/password/random")
@admin_required
def user_password_random(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    password = make_password()
    with connect() as conn:
        user = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return "Not Found", 404
        conn.execute(
            "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
            (generate_password_hash(password, method="scrypt"), utcnow(), user_id),
        )
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user_id,))
        conn.commit()
    session["one_time_secret"] = {
        "title": f"{user['username']} 的临时密码",
        "value": password,
        "message": "密码只显示这一次，请现在复制保存。旧密码和旧登录状态已经失效。",
    }
    flash(f"{user['username']} 的密码已随机重置", "success")
    return _users_redirect()


@admin_bp.post("/users/<int:user_id>/delete")
@admin_required
def user_delete(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with transaction() as conn:
        conn.execute("UPDATE invites SET used_by=NULL WHERE used_by=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    flash("用户已删除", "success")
    return _users_redirect()


@admin_bp.get("/invites")
@admin_required
def invites():
    page = _page_arg()
    per_page = 20
    query = request.args.get("q", "").strip()[:64]
    status_filter = request.args.get("status", "all").strip().lower()
    if status_filter not in {"all", "active", "used", "revoked"}:
        status_filter = "all"
    where = []
    params = []
    if query:
        where.append("invites.code LIKE ? COLLATE NOCASE")
        params.append(f"%{query}%")
    if status_filter != "all":
        where.append("invites.status=?")
        params.append(status_filter)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM invites{where_sql}", params).fetchone()[0])
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = conn.execute(
            f"""SELECT invites.*,
                      COUNT(users.id) current_user_count,
                      GROUP_CONCAT(users.username, '、') current_usernames
               FROM invites
               LEFT JOIN users ON users.invite_id=invites.id
               {where_sql}
               GROUP BY invites.id
               ORDER BY invites.id DESC LIMIT ? OFFSET ?""",
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()
    return render_template(
        "invites.html", invites=rows, total_invites=total, page=page, pages=pages,
        query=query, status_filter=status_filter, per_page=per_page,
    )


def _valid_custom_invite(code):
    if len(code) < 2 or len(code) > 32:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in code)


@admin_bp.post("/invites/create")
@admin_required
def invite_create():
    if not require_csrf():
        return "CSRF validation failed", 400
    code = request.form.get("code", "").strip()
    if not _valid_custom_invite(code):
        flash("邀请码需为 2-32 位，可使用中文、英文字母、数字、- 和 _", "error")
        return _invites_redirect()
    try:
        max_uses = int(request.form.get("max_uses", "1"))
    except ValueError:
        max_uses = 0
    if max_uses < 1 or max_uses > 10000:
        flash("可用次数需为 1-10000", "error")
        return _invites_redirect()
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO invites(code,status,max_uses,use_count,created_at) VALUES(?,?,?,?,?)",
                (code, "active", max_uses, 0, utcnow()),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            flash("这个邀请码已经存在，请换一个", "error")
            return _invites_redirect()
        raise
    flash(f"邀请码已创建：{code}，可使用 {max_uses} 次", "success")
    return _invites_redirect()


@admin_bp.post("/invites/<int:invite_id>/revoke")
@admin_required
def invite_revoke(invite_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with connect() as conn:
        conn.execute("UPDATE invites SET status='revoked' WHERE id=? AND status='active'", (invite_id,))
        conn.commit()
    return _invites_redirect()


@admin_bp.post("/invites/<int:invite_id>/delete")
@admin_required
def invite_delete(invite_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with transaction() as conn:
        row = conn.execute("SELECT use_count FROM invites WHERE id=?", (invite_id,)).fetchone()
        if not row:
            return "Not Found", 404
        # Users keep their accounts; the invite relation is intentionally cleared.
        conn.execute("UPDATE users SET invite_id=NULL WHERE invite_id=?", (invite_id,))
        conn.execute("DELETE FROM invites WHERE id=?", (invite_id,))
    flash("邀请码记录已删除；已注册用户不受影响", "success")
    return _invites_redirect()


@admin_bp.get("/app-update")
@admin_required
def app_update_admin():
    values = get_settings()
    release_result = get_app_release(current_app)
    snapshot = release_result.get("snapshot") if release_result else None
    history_result = get_release_history(current_app)
    history_releases = history_result.get("releases", []) if history_result else []
    try:
        current_min_version_code = max(0, int(values.get("app_update_min_version_code", "0") or 0))
    except (TypeError, ValueError):
        current_min_version_code = 0
    latest_version_code = int(snapshot.get("version_code") or 0) if snapshot else 0
    for item in history_releases:
        code = int(item.get("version_code") or 0)
        item["policy_selectable"] = bool(item.get("selectable") and latest_version_code and code <= latest_version_code)
        item["above_latest"] = bool(item.get("selectable") and latest_version_code and code > latest_version_code)
    min_code_in_history = any(
        int(item.get("version_code") or 0) == current_min_version_code
        for item in history_releases
        if item.get("policy_selectable")
    )
    return render_template(
        "app_update.html",
        settings_values=values,
        release_result=release_result,
        app_release=snapshot,
        android_repository=values.get("app_update_repository", "kkx999/XVPN-Android"),
        history_result=history_result,
        history_releases=history_releases,
        current_min_version_code=current_min_version_code,
        min_code_in_history=min_code_in_history,
        cache_seconds=CACHE_SECONDS,
        check_interval_seconds=CHECK_INTERVAL_SECONDS,
    )


@admin_bp.post("/app-update/settings")
@admin_required
def app_update_settings():
    if not require_csrf():
        return "CSRF validation failed", 400
    repository = (request.form.get("app_update_repository") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        flash("Android Release 仓库格式应为 owner/repo，例如 kkx999/XVPN-Android", "error")
        return redirect(url_for("admin.app_update_admin"))
    try:
        min_version_code = int(request.form.get("app_update_min_version_code", "0") or 0)
    except ValueError:
        min_version_code = -1
    if min_version_code < 0 or min_version_code > 2147483647:
        flash("最低允许运行版本参数无效，请从下拉列表重新选择", "error")
        return redirect(url_for("admin.app_update_admin"))
    enabled = "1" if request.form.get("app_update_enabled") == "1" else "0"
    force_update = "1" if request.form.get("app_update_force") == "1" else "0"
    current_values = get_settings()
    repository_changed = repository != (current_values.get("app_update_repository") or "kkx999/XVPN-Android")
    if not repository_changed and min_version_code:
        release_result = get_app_release(current_app)
        history_result = get_release_history(current_app)
        latest = release_result.get("snapshot") if release_result and release_result.get("ok") else None
        latest_code = int(latest.get("version_code") or 0) if latest else 0
        allowed_codes = {
            int(item.get("version_code") or 0)
            for item in (history_result.get("releases", []) if history_result and history_result.get("ok") else [])
            if item.get("selectable") and int(item.get("version_code") or 0) > 0 and latest_code and int(item.get("version_code") or 0) <= latest_code
        }
        old_min = max(0, int(current_values.get("app_update_min_version_code", "0") or 0))
        if min_version_code not in allowed_codes and min_version_code != old_min:
            flash("最低允许运行版本必须从当前可用历史版本中选择，且不能高于当前 Latest Release", "error")
            return redirect(url_for("admin.app_update_admin"))
    updates = {
        "app_update_repository": repository,
        "app_update_enabled": enabled,
        "app_update_force": force_update,
        "app_update_min_version_code": str(min_version_code),
    }
    if repository_changed:
        # A repository switch must never keep serving the old repository snapshot
        # or reuse a minimum versionCode that belonged to another Android app line.
        updates.update({
            "app_update_min_version_code": "0",
            "app_update_last_checked_at": "",
            "app_update_last_status": "仓库已更改，等待同步 Latest Release",
            "app_update_last_snapshot_json": "",
            "app_update_last_stale": "0",
            "app_update_last_warning": "",
            "app_update_history_checked_at": "",
            "app_update_release_history_json": "",
            "app_update_history_stale": "0",
            "app_update_history_warning": "",
        })
    set_settings(updates)
    effective_min = 0 if repository_changed else min_version_code
    log_event("app_update", "info", f"App 更新策略已保存：repository={repository}, enabled={enabled}, force={force_update}, min_version_code={effective_min}")
    flash("App 更新策略已保存" + ("，仓库已切换、旧缓存已清除，最低运行版本已重置为不限制" if repository_changed else ""), "success")
    return redirect(url_for("admin.app_update_admin"))


@admin_bp.post("/app-update/refresh")
@admin_required
def app_update_refresh():
    if not require_csrf():
        return "CSRF validation failed", 400
    result = get_app_release(current_app, force=True)
    history_result = get_release_history(current_app, force=True)
    if result.get("ok") and result.get("snapshot"):
        tag = result["snapshot"].get("tag") or result["snapshot"].get("version_name")
        if result.get("stale"):
            log_event("app_update", "error", f"Android Release 同步失败，继续使用缓存：{tag}")
            flash(f"同步失败，继续使用上一次缓存：{tag}", "error")
        else:
            history_count = len(history_result.get("releases", [])) if history_result.get("ok") else 0
            log_event("app_update", "success", f"已同步 Android Latest Release：{tag}；历史版本 {history_count} 个")
            suffix = f"，历史版本 {history_count} 个" if history_result.get("ok") else "，历史版本同步失败"
            flash(f"已同步 Android Latest Release：{tag}{suffix}", "success")
    else:
        error = result.get("error", "未知错误")
        log_event("app_update", "error", f"同步 Android Release 失败：{error}")
        flash(f"同步 Android Release 失败：{error}", "error")
    return redirect(url_for("admin.app_update_admin"))


@admin_bp.get("/settings")
@admin_required
def settings():
    values = get_settings()
    backup_page = _page_arg("backup_page")
    backup_per_page = 10
    all_backups = list_backups()
    backup_total = len(all_backups)
    backup_pages = max(1, (backup_total + backup_per_page - 1) // backup_per_page)
    backup_page = min(backup_page, backup_pages)
    backups = all_backups[(backup_page - 1) * backup_per_page:backup_page * backup_per_page]
    runtime_events = list_events(10)
    db_path = Path(current_app.config["DATABASE_PATH"])
    with connect() as conn:
        latest_traffic = conn.execute(
            """SELECT t.last_report_at,u.username,t.app_version
               FROM traffic_device_counters t JOIN users u ON u.id=t.user_id
               ORDER BY t.last_report_at DESC LIMIT 1"""
        ).fetchone()
        traffic_users = int(conn.execute("SELECT COUNT(DISTINCT user_id) FROM traffic_device_counters").fetchone()[0])
    system_info = {
        "host": request.host,
        "database": str(db_path),
        "database_size": db_path.stat().st_size if db_path.exists() else 0,
        "https": request.is_secure,
    }
    return render_template(
        "settings.html",
        settings_values=values,
        backups=backups,
        system_info=system_info,
        runtime_events=runtime_events,
        latest_traffic=latest_traffic,
        traffic_users=traffic_users,
        backup_page=backup_page, backup_pages=backup_pages, backup_total=backup_total,
        telegram_configured=bool(values.get("telegram_bot_token_enc") and values.get("telegram_chat_id")),
    )


@admin_bp.post("/settings/system")
@admin_required
def settings_system():
    if not require_csrf():
        return "CSRF validation failed", 400
    panel_name = request.form.get("panel_name", "").strip()
    panel_subtitle = request.form.get("panel_subtitle", "").strip()
    try:
        token_days = int(request.form.get("token_days", "30"))
    except ValueError:
        token_days = 0
    if not panel_name or len(panel_name) > 40:
        flash("面板名称需为 1-40 位", "error")
        return redirect(url_for("admin.settings"))
    if len(panel_subtitle) > 80:
        flash("面板副标题最多 80 位", "error")
        return redirect(url_for("admin.settings"))
    if token_days < 1 or token_days > 3650:
        flash("登录有效期需为 1-3650 天", "error")
        return redirect(url_for("admin.settings"))
    panel_timezone = request.form.get("panel_timezone", "UTC").strip() or "UTC"
    try:
        ZoneInfo(panel_timezone)
    except ZoneInfoNotFoundError:
        flash("面板时区无效，请使用例如 Asia/Shanghai、Asia/Hong_Kong 或 America/Los_Angeles", "error")
        return redirect(url_for("admin.settings"))
    values = {
        "panel_name": panel_name,
        "panel_subtitle": panel_subtitle or "私人访问控制台",
        "token_days": str(token_days),
        "registration_enabled": "1" if "1" in request.form.getlist("registration_enabled") else "0",
        "panel_timezone": panel_timezone,
        "backup_timezone": panel_timezone,
    }
    set_settings(values)
    apply_settings(current_app)
    flash("系统设置已保存并立即生效", "success")
    return redirect(url_for("admin.settings"))



@admin_bp.post("/settings/backup-automation")
@admin_required
def backup_automation_settings():
    if not require_csrf():
        return "CSRF validation failed", 400
    interval = request.form.get("backup_interval", "daily")
    if interval not in {"6h", "12h", "daily", "3d", "weekly"}:
        interval = "daily"
    backup_time = request.form.get("backup_time", "04:00").strip()
    try:
        hour, minute = [int(x) for x in backup_time.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        backup_time = f"{hour:02d}:{minute:02d}"
    except Exception:
        flash("自动备份执行时间格式不正确", "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    try:
        keep = int(request.form.get("backup_keep", "7"))
    except ValueError:
        keep = 0
    if keep < 1 or keep > 100:
        flash("自动备份本地保留数量需为 1-100", "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    current = get_settings()
    tz_name = current.get("panel_timezone") or current.get("backup_timezone") or "UTC"
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "UTC"
    bot_token = request.form.get("telegram_bot_token", "").strip()
    token_enc = current.get("telegram_bot_token_enc", "")
    if bot_token:
        token_enc = encrypt_text(current_app, bot_token)
    chat_id = request.form.get("telegram_chat_id", "").strip()
    telegram_enabled = "1" if request.form.get("telegram_enabled") == "1" else "0"
    if telegram_enabled == "1" and (not token_enc or not chat_id):
        flash("开启 Telegram 发送前，请先填写 Bot Token 和 Chat ID", "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    values = {
        "auto_backup_enabled": "1" if request.form.get("auto_backup_enabled") == "1" else "0",
        "backup_interval": interval,
        "backup_time": backup_time,
        "backup_keep": str(keep),
        "backup_timezone": tz_name,
        "telegram_enabled": telegram_enabled,
        "telegram_chat_id": chat_id,
        "telegram_bot_token_enc": token_enc,
    }
    set_settings(values)
    flash("自动备份与 Telegram 设置已保存", "success")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.post("/settings/telegram/test")
@admin_required
def telegram_test():
    if not require_csrf():
        return "CSRF validation failed", 400
    current = get_settings()
    supplied_token = request.form.get("telegram_bot_token", "").strip()
    supplied_chat = request.form.get("telegram_chat_id", "").strip()
    token = supplied_token
    if not token and current.get("telegram_bot_token_enc"):
        try:
            token = decrypt_text(current_app, current["telegram_bot_token_enc"])
        except Exception:
            token = ""
    chat_id = supplied_chat or current.get("telegram_chat_id", "")
    try:
        bot_name = telegram_test_connection(token=token, chat_id=chat_id)
        updates = {"telegram_last_status": f"测试成功：@{bot_name}"}
        if supplied_token:
            updates["telegram_bot_token_enc"] = encrypt_text(current_app, supplied_token)
        if supplied_chat:
            updates["telegram_chat_id"] = supplied_chat
        set_settings(updates)
        log_event("telegram", "success", f"Telegram 测试成功：@{bot_name}")
        flash(f"Telegram 测试成功：{bot_name}；本次有效的 Token / Chat ID 已安全保存", "success")
    except ValueError as exc:
        set_settings({"telegram_last_status": f"测试失败：{exc}"})
        log_event("telegram", "error", f"Telegram 测试失败：{exc}")
        flash(str(exc), "error")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.post("/settings/telegram/clear")
@admin_required
def telegram_clear():
    if not require_csrf():
        return "CSRF validation failed", 400
    set_settings({
        "telegram_enabled": "0",
        "telegram_bot_token_enc": "",
        "telegram_chat_id": "",
        "telegram_last_status": "Telegram 配置已清除",
    })
    log_event("telegram", "info", "Telegram 配置已清除")
    flash("Telegram 配置已清除", "success")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.post("/settings/runtime-logs/clear")
@admin_required
def runtime_logs_clear():
    if not require_csrf():
        return "CSRF validation failed", 400
    clear_events()
    flash("运行记录已清除；当前运行状态和用户流量统计不受影响", "success")
    return redirect(url_for("admin.settings") + "#runtime-status")


@admin_bp.post("/settings/backups/create")
@admin_required
def backup_create():
    if not require_csrf():
        return "CSRF validation failed", 400
    try:
        path = create_backup("manual")
        log_event("backup", "success", f"手动备份成功：{path.name}")
        flash(f"手动备份已创建：{path.name}", "success")
    except Exception as exc:
        log_event("backup", "error", f"手动备份失败：{exc}")
        flash(f"手动备份失败：{exc}", "error")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.get("/settings/backups/<path:name>/download")
@admin_required
def backup_download(name):
    try:
        path = get_backup_path(name)
    except ValueError:
        return "Not Found", 404
    if not path.is_file():
        return "Not Found", 404
    return send_file(path, as_attachment=True, download_name=path.name)


@admin_bp.post("/settings/backups/<path:name>/restore")
@admin_required
def backup_restore(name):
    if not require_csrf():
        return "CSRF validation failed", 400
    try:
        path = get_backup_path(name)
        restore_backup(path)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    flash("备份恢复完成；恢复前的数据已自动再备份一份", "success")
    return _settings_backup_redirect()


@admin_bp.post("/settings/backups/<path:name>/delete")
@admin_required
def backup_delete(name):
    if not require_csrf():
        return "CSRF validation failed", 400
    try:
        delete_backup(name)
    except ValueError:
        return "Not Found", 404
    flash("备份文件已删除", "success")
    return _settings_backup_redirect()


@admin_bp.post("/settings/backups/upload")
@admin_required
def backup_upload_restore():
    if not require_csrf():
        return "CSRF validation failed", 400
    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        flash("请选择备份 ZIP 文件", "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    if request.content_length and request.content_length > 50 * 1024 * 1024:
        flash("备份文件不能超过 50MB", "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    temp_name = f"xvpn-panel-upload-{secrets.token_hex(6)}.zip"
    temp_path = get_backup_path(temp_name)
    upload.save(temp_path)
    try:
        restore_backup(temp_path)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.settings") + "#backup-section")
    finally:
        if temp_path.exists():
            temp_path.unlink()
    flash("上传的备份已恢复；恢复前的数据已自动备份", "success")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.post("/settings/username")
@admin_required
def admin_username():
    if not require_csrf():
        return "CSRF validation failed", 400
    new_username = request.form.get("new_username", "").strip()
    current_password = request.form.get("current_password", "")
    if len(new_username) < 3 or len(new_username) > 32:
        flash("管理员用户名长度需为 3-32 位", "error")
        return redirect(url_for("admin.settings"))
    if not new_username.replace("_", "").replace("-", "").isalnum():
        flash("管理员用户名只能包含字母、数字、下划线和短横线", "error")
        return redirect(url_for("admin.settings"))
    with transaction() as conn:
        admin = conn.execute("SELECT * FROM admins WHERE id=?", (session["admin_id"],)).fetchone()
        if not admin or not check_password_hash(admin["password_hash"], current_password):
            flash("当前密码错误", "error")
            return redirect(url_for("admin.settings"))
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (new_username,)).fetchone():
            flash("该用户名已被 App 用户使用，请更换", "error")
            return redirect(url_for("admin.settings"))
        conflict = conn.execute("SELECT 1 FROM admins WHERE username=? COLLATE NOCASE AND id<>?", (new_username, admin["id"])).fetchone()
        if conflict:
            flash("管理员用户名已存在", "error")
            return redirect(url_for("admin.settings"))
        if new_username.lower() == str(admin["username"]).lower():
            flash("管理员用户名没有变化", "error")
            return redirect(url_for("admin.settings"))
        next_version = int(admin["session_version"]) + 1
        conn.execute(
            "UPDATE admins SET username=?,updated_at=?,session_version=? WHERE id=?",
            (new_username, utcnow(), next_version, admin["id"]),
        )
        conn.execute("DELETE FROM admin_api_tokens WHERE admin_id=?", (admin["id"],))
    session["admin_username"] = new_username
    session["admin_session_version"] = next_version
    flash("管理员用户名已修改；其他后台和 App 登录状态已失效", "success")
    return redirect(url_for("admin.settings"))


@admin_bp.post("/settings/password")
@admin_required
def admin_password():
    if not require_csrf():
        return "CSRF validation failed", 400
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    if len(new_password) < 8:
        flash("新密码至少 8 位", "error")
        return redirect(url_for("admin.settings"))
    if new_password != confirm_password:
        flash("两次输入的新密码不一致", "error")
        return redirect(url_for("admin.settings"))
    with connect() as conn:
        admin = conn.execute("SELECT * FROM admins WHERE id=?", (session["admin_id"],)).fetchone()
        if not admin or not check_password_hash(admin["password_hash"], current_password):
            flash("当前密码错误", "error")
            return redirect(url_for("admin.settings"))
        next_version = int(admin["session_version"]) + 1
        conn.execute(
            "UPDATE admins SET password_hash=?,updated_at=?,session_version=? WHERE id=?",
            (generate_password_hash(new_password, method="scrypt"), utcnow(), next_version, admin["id"]),
        )
        conn.execute("DELETE FROM admin_api_tokens WHERE admin_id=?", (admin["id"],))
        conn.commit()
    session["admin_session_version"] = next_version
    flash("管理员密码已修改，其他后台登录状态已失效", "success")
    return redirect(url_for("admin.settings"))
