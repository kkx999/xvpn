import base64
import json
import secrets
import string
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
        "one_time_secret": session.pop("one_time_secret", None),
    }


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        if not require_csrf():
            return "CSRF validation failed", 400
        with connect() as conn:
            admin = conn.execute(
                "SELECT * FROM admins WHERE username=? COLLATE NOCASE",
                (request.form.get("username", "").strip(),),
            ).fetchone()
        if admin and check_password_hash(admin["password_hash"], request.form.get("password", "")):
            session.clear()
            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]
            session["admin_session_version"] = int(admin["session_version"])
            csrf_token()
            return redirect(url_for("admin.dashboard"))
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
        recent_users = conn.execute(
            "SELECT username,status,created_at,last_login_at FROM users ORDER BY id DESC LIMIT 6"
        ).fetchall()
    return render_template("dashboard.html", stats=stats, recent_users=recent_users)


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
    with connect() as conn:
        rows = conn.execute(
            """SELECT users.*, invites.code invite_code
               FROM users LEFT JOIN invites ON invites.id=users.invite_id
               ORDER BY users.id DESC"""
        ).fetchall()
    return render_template("users.html", users=rows)


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
    return redirect(url_for("admin.users"))


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
        return redirect(url_for("admin.users"))
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
    return redirect(url_for("admin.users"))


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
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/delete")
@admin_required
def user_delete(user_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with transaction() as conn:
        conn.execute("UPDATE invites SET used_by=NULL WHERE used_by=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    flash("用户已删除", "success")
    return redirect(url_for("admin.users"))


@admin_bp.get("/invites")
@admin_required
def invites():
    with connect() as conn:
        rows = conn.execute(
            """SELECT invites.*,
                      COUNT(users.id) current_user_count,
                      GROUP_CONCAT(users.username, '、') current_usernames
               FROM invites
               LEFT JOIN users ON users.invite_id=invites.id
               GROUP BY invites.id
               ORDER BY invites.id DESC"""
        ).fetchall()
    return render_template("invites.html", invites=rows)


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
        return redirect(url_for("admin.invites"))
    try:
        max_uses = int(request.form.get("max_uses", "1"))
    except ValueError:
        max_uses = 0
    if max_uses < 1 or max_uses > 10000:
        flash("可用次数需为 1-10000", "error")
        return redirect(url_for("admin.invites"))
    try:
        with transaction() as conn:
            conn.execute(
                "INSERT INTO invites(code,status,max_uses,use_count,created_at) VALUES(?,?,?,?,?)",
                (code, "active", max_uses, 0, utcnow()),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            flash("这个邀请码已经存在，请换一个", "error")
            return redirect(url_for("admin.invites"))
        raise
    flash(f"邀请码已创建：{code}，可使用 {max_uses} 次", "success")
    return redirect(url_for("admin.invites"))


@admin_bp.post("/invites/<int:invite_id>/revoke")
@admin_required
def invite_revoke(invite_id):
    if not require_csrf():
        return "CSRF validation failed", 400
    with connect() as conn:
        conn.execute("UPDATE invites SET status='revoked' WHERE id=? AND status='active'", (invite_id,))
        conn.commit()
    return redirect(url_for("admin.invites"))


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
    return redirect(url_for("admin.invites"))


@admin_bp.get("/settings")
@admin_required
def settings():
    values = get_settings()
    backups = list_backups()
    db_path = Path(current_app.config["DATABASE_PATH"])
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
    values = {
        "panel_name": panel_name,
        "panel_subtitle": panel_subtitle or "私人访问控制台",
        "token_days": str(token_days),
        "registration_enabled": "1" if request.form.get("registration_enabled") == "1" else "0",
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
    tz_name = request.form.get("backup_timezone", "UTC").strip() or "UTC"
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "UTC"

    current = get_settings()
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
        flash(f"Telegram 测试成功：{bot_name}；本次有效的 Token / Chat ID 已安全保存", "success")
    except ValueError as exc:
        set_settings({"telegram_last_status": f"测试失败：{exc}"})
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
    flash("Telegram 配置已清除", "success")
    return redirect(url_for("admin.settings") + "#backup-section")


@admin_bp.post("/settings/backups/download-new")
@admin_required
def backup_download_new():
    if not require_csrf():
        return "CSRF validation failed", 400
    path = create_backup("manual")
    return send_file(path, as_attachment=True, download_name=path.name)


@admin_bp.post("/settings/backups/create")
@admin_required
def backup_create():
    if not require_csrf():
        return "CSRF validation failed", 400
    path = create_backup("manual")
    flash(f"备份已创建：{path.name}", "success")
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
    return redirect(url_for("admin.settings") + "#backup-section")


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
    return redirect(url_for("admin.settings") + "#backup-section")


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
    temp_name = f"vpn-panel-upload-{secrets.token_hex(6)}.zip"
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
        conn.commit()
    session["admin_session_version"] = next_version
    flash("管理员密码已修改，其他后台登录状态已失效", "success")
    return redirect(url_for("admin.settings"))
