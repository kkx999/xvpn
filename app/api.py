import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .app_updates import app_update_payload
from .crypto import decrypt_text
from .db import connect, transaction, utcnow
from .node_profile import canonical_profile
from .traffic import traffic_period_keys, traffic_summary
from .version import APP_VERSION
from .settings_store import get_settings


api_bp = Blueprint("api", __name__)


@api_bp.after_request
def api_security_headers(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def response_error(code, message, status=400):
    return jsonify({"ok": False, "code": code, "message": message}), status


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _rate_key(action, identity):
    raw = f"{action}:{request.remote_addr or 'unknown'}:{identity or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _rate_check(action, identity, max_attempts=10, window_seconds=900, block_seconds=900):
    key = _rate_key(action, identity)
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute("SELECT * FROM auth_rate_limits WHERE rate_key=?", (key,)).fetchone()
        if not row:
            return None
        if row["blocked_until"]:
            blocked = datetime.fromisoformat(row["blocked_until"])
            if blocked > now:
                return max(1, int((blocked - now).total_seconds()))
        started = datetime.fromisoformat(row["window_started_at"])
        if (now - started).total_seconds() > window_seconds:
            conn.execute("DELETE FROM auth_rate_limits WHERE rate_key=?", (key,))
            conn.commit()
            return None
        if int(row["attempts"]) >= max_attempts:
            blocked = now + timedelta(seconds=block_seconds)
            conn.execute(
                "UPDATE auth_rate_limits SET blocked_until=? WHERE rate_key=?",
                (blocked.isoformat(timespec="seconds"), key),
            )
            conn.commit()
            return block_seconds
    return None


def _rate_fail(action, identity, max_attempts=10, window_seconds=900, block_seconds=900, conn=None):
    key = _rate_key(action, identity)
    now = datetime.now(timezone.utc)
    own_conn = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT * FROM auth_rate_limits WHERE rate_key=?", (key,)).fetchone()
        if not row or (now - datetime.fromisoformat(row["window_started_at"])).total_seconds() > window_seconds:
            conn.execute(
                "INSERT OR REPLACE INTO auth_rate_limits(rate_key,attempts,window_started_at,blocked_until) VALUES(?,?,?,NULL)",
                (key, 1, now.isoformat(timespec="seconds")),
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
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def _rate_reset(action, identity):
    with connect() as conn:
        conn.execute("DELETE FROM auth_rate_limits WHERE rate_key=?", (_rate_key(action, identity),))
        conn.commit()


def _issue_token(conn, user_id):
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM api_tokens WHERE expires_at<=?", (now.isoformat(timespec="seconds"),))
    raw = secrets.token_urlsafe(48)
    expires = now + timedelta(days=current_app.config["TOKEN_DAYS"])
    conn.execute(
        "INSERT INTO api_tokens(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
        (user_id, token_hash(raw), expires.isoformat(timespec="seconds"), utcnow()),
    )
    return raw, expires


def _issue_admin_token(conn, admin_id):
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM admin_api_tokens WHERE expires_at<=?", (now.isoformat(timespec="seconds"),))
    raw = secrets.token_urlsafe(48)
    expires = now + timedelta(days=current_app.config["TOKEN_DAYS"])
    conn.execute(
        "INSERT INTO admin_api_tokens(admin_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
        (admin_id, token_hash(raw), expires.isoformat(timespec="seconds"), utcnow()),
    )
    return raw, expires


def bearer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return response_error("UNAUTHORIZED", "请先登录", 401)
        raw = auth[7:].strip()
        hashed = token_hash(raw)
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute(
                """SELECT api_tokens.*,users.username,users.status user_status
                   FROM api_tokens JOIN users ON users.id=api_tokens.user_id
                   WHERE token_hash=?""",
                (hashed,),
            ).fetchone()
            if row:
                if datetime.fromisoformat(row["expires_at"]) <= now:
                    conn.execute("DELETE FROM api_tokens WHERE id=?", (row["id"],))
                    conn.commit()
                    return response_error("TOKEN_EXPIRED", "登录已过期", 401)
                if row["user_status"] != "active":
                    conn.execute("DELETE FROM api_tokens WHERE user_id=?", (row["user_id"],))
                    conn.commit()
                    return response_error("ACCOUNT_DISABLED", "账户已停用", 403)
                conn.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (utcnow(), row["id"]))
                conn.commit()
                g.user = {"id": row["user_id"], "username": row["username"], "token": raw, "role": "user"}
                blocked = _version_policy_response()
                return blocked if blocked is not None else view(*args, **kwargs)

            admin = conn.execute(
                """SELECT admin_api_tokens.*,admins.username
                   FROM admin_api_tokens JOIN admins ON admins.id=admin_api_tokens.admin_id
                   WHERE token_hash=?""",
                (hashed,),
            ).fetchone()
            if not admin:
                return response_error("UNAUTHORIZED", "登录状态已失效", 401)
            if datetime.fromisoformat(admin["expires_at"]) <= now:
                conn.execute("DELETE FROM admin_api_tokens WHERE id=?", (admin["id"],))
                conn.commit()
                return response_error("TOKEN_EXPIRED", "登录已过期", 401)
            conn.execute("UPDATE admin_api_tokens SET last_used_at=? WHERE id=?", (utcnow(), admin["id"]))
            conn.commit()
            g.user = {"id": admin["admin_id"], "username": admin["username"], "token": raw, "role": "admin"}
        blocked = _version_policy_response()
        return blocked if blocked is not None else view(*args, **kwargs)
    return wrapped


def _client_version():
    name = str(
        request.headers.get("X-XVPN-Version-Name") or
        request.headers.get("X-App-Version") or request.headers.get("X-Version-Name") or ""
    ).strip()[:64]
    raw = (
        request.headers.get("X-XVPN-Version-Code") or
        request.headers.get("X-App-Version-Code") or request.headers.get("X-Version-Code") or "0"
    )
    try:
        code = max(0, int(raw or 0))
    except (TypeError, ValueError):
        code = 0
    return name, code


def _version_policy_response():
    settings = get_settings()
    try:
        minimum = max(0, int(settings.get("app_update_min_version_code", "0") or 0))
    except (TypeError, ValueError):
        minimum = 0
    force = settings.get("app_update_force", "0") == "1"
    name, code = _client_version()
    if not force and (not minimum or not code or code >= minimum):
        return None
    payload = app_update_payload(current_app, name, code)
    if not payload.get("must_update") and not payload.get("force_update"):
        return None
    payload.update({
        "ok": False,
        "code": "APP_VERSION_UNSUPPORTED",
        "message": "当前 App 版本低于服务器最低要求，请先更新后继续使用",
    })
    return jsonify(payload), 426


@api_bp.get("")
@api_bp.get("/")
def api_index():
    return {
        "ok": True,
        "service": current_app.config["PANEL_NAME"],
        "api": "v1",
        "version": APP_VERSION,
        "core": "mihomo",
        "node_schema": "xvpn.node.v1",
        "registration_enabled": bool(current_app.config.get("REGISTRATION_ENABLED", True)),
        "token_days": int(current_app.config.get("TOKEN_DAYS", 30)),
        "traffic_reporting": True,
        "traffic_report_interval_seconds": 10,
        "traffic_report_requires_node_id": True,
        "panel_timezone": current_app.config.get("PANEL_TIMEZONE", "UTC"),
        "app_update_api": True,
        "app_update_check_interval_seconds": 43200,
    }


@api_bp.get("/health")
def health():
    return {
        "ok": True,
        "service": current_app.config["PANEL_NAME"],
        "version": APP_VERSION,
        "core": "mihomo",
        "node_schema": "xvpn.node.v1",
    }


@api_bp.get("/app/update")
def app_update():
    version_name = str(
        request.args.get("version_name") or request.args.get("versionName") or
        request.args.get("current_version") or request.args.get("currentVersion") or
        request.headers.get("X-App-Version") or request.headers.get("X-Version-Name") or ""
    ).strip()[:64]
    raw_code = (
        request.args.get("version_code") or request.args.get("versionCode") or
        request.args.get("current_version_code") or request.args.get("currentVersionCode") or
        request.headers.get("X-App-Version-Code") or request.headers.get("X-Version-Code") or "0"
    )
    try:
        version_code = max(0, int(raw_code or 0))
    except (TypeError, ValueError):
        return response_error("INVALID_VERSION_CODE", "version_code 必须是非负整数")
    payload = app_update_payload(current_app, version_name, version_code)
    return jsonify(payload), 200 if payload.get("ok") else 503


@api_bp.post("/register")
def register():
    if not current_app.config.get("REGISTRATION_ENABLED", True):
        return response_error("REGISTRATION_CLOSED", "当前已暂停新用户注册", 403)
    retry_after = _rate_check("register", "invite", max_attempts=20, window_seconds=900, block_seconds=900)
    if retry_after:
        return jsonify({"ok": False, "code": "RATE_LIMITED", "message": "注册尝试过多，请稍后再试", "retry_after": retry_after}), 429

    data = request.get_json(silent=True) or {}
    code = str(data.get("invite_code", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    if len(username) < 3 or len(username) > 32:
        return response_error("INVALID_USERNAME", "用户名长度需为 3-32 位")
    if not username.replace("_", "").replace("-", "").isalnum():
        return response_error("INVALID_USERNAME", "用户名只能包含字母、数字、下划线和短横线")
    if len(password) < 8:
        return response_error("WEAK_PASSWORD", "密码至少 8 位")

    with transaction() as conn:
        invite = conn.execute("SELECT * FROM invites WHERE code=? COLLATE NOCASE", (code,)).fetchone()
        if not invite or invite["status"] != "active" or int(invite["use_count"]) >= int(invite["max_uses"]):
            _rate_fail("register", "invite", max_attempts=20, window_seconds=900, block_seconds=900, conn=conn)
            return response_error("INVALID_INVITE", "邀请码无效、已作废或使用次数已用完", 403)
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone() or conn.execute(
            "SELECT 1 FROM admins WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone():
            return response_error("USERNAME_EXISTS", "用户名已存在", 409)
        cur = conn.execute(
            "INSERT INTO users(username,password_hash,status,invite_id,created_at) VALUES(?,?,?,?,?)",
            (username, generate_password_hash(password, method="scrypt"), "active", invite["id"], utcnow()),
        )
        user_id = cur.lastrowid
        next_count = int(invite["use_count"]) + 1
        next_status = "used" if next_count >= int(invite["max_uses"]) else "active"
        conn.execute(
            "UPDATE invites SET status=?,use_count=?,used_by=?,used_at=? WHERE id=?",
            (next_status, next_count, user_id, utcnow(), invite["id"]),
        )
    _rate_reset("register", "invite")
    return jsonify({"ok": True, "message": "注册成功"}), 201


@api_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    retry_after = _rate_check("login", username, max_attempts=10, window_seconds=900, block_seconds=900)
    if retry_after:
        return jsonify({"ok": False, "code": "RATE_LIMITED", "message": "登录尝试过多，请稍后再试", "retry_after": retry_after}), 429

    with connect() as conn:
        admin = conn.execute("SELECT * FROM admins WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if admin and check_password_hash(admin["password_hash"], password):
            raw, expires = _issue_admin_token(conn, admin["id"])
            principal = {"id": admin["id"], "username": admin["username"], "role": "admin"}
            conn.commit()
        elif user and check_password_hash(user["password_hash"], password):
            if user["status"] != "active":
                return response_error("ACCOUNT_DISABLED", "账户已停用", 403)
            raw, expires = _issue_token(conn, user["id"])
            conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (utcnow(), user["id"]))
            principal = {"id": user["id"], "username": user["username"], "role": "user"}
            conn.commit()
        else:
            _rate_fail("login", username, max_attempts=10, window_seconds=900, block_seconds=900)
            return response_error("INVALID_CREDENTIALS", "用户名或密码错误", 401)
    _rate_reset("login", username)
    return jsonify({"ok": True, "token": raw, "expires_at": expires.isoformat(timespec="seconds"), "user": principal})


@api_bp.post("/logout")
@bearer_required
def logout():
    with connect() as conn:
        table = "admin_api_tokens" if g.user.get("role") == "admin" else "api_tokens"
        conn.execute(f"DELETE FROM {table} WHERE token_hash=?", (token_hash(g.user["token"]),))
        conn.commit()
    return {"ok": True}


@api_bp.get("/me")
@bearer_required
def me():
    return {
        "ok": True,
        "user": {"id": g.user["id"], "username": g.user["username"], "status": "active", "role": g.user.get("role", "user")},
    }


@api_bp.post("/change-password")
@bearer_required
def change_password():
    data = request.get_json(silent=True) or {}
    current_password = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))
    if len(new_password) < 8:
        return response_error("WEAK_PASSWORD", "新密码至少 8 位")
    if current_password == new_password:
        return response_error("PASSWORD_UNCHANGED", "新密码不能与当前密码相同")

    with transaction() as conn:
        if g.user.get("role") == "admin":
            admin = conn.execute("SELECT * FROM admins WHERE id=?", (g.user["id"],)).fetchone()
            if not admin or not check_password_hash(admin["password_hash"], current_password):
                return response_error("INVALID_CURRENT_PASSWORD", "当前密码错误", 401)
            next_version = int(admin["session_version"]) + 1
            conn.execute(
                "UPDATE admins SET password_hash=?,updated_at=?,session_version=? WHERE id=?",
                (generate_password_hash(new_password, method="scrypt"), utcnow(), next_version, admin["id"]),
            )
            conn.execute("DELETE FROM admin_api_tokens WHERE admin_id=?", (admin["id"],))
            raw, expires = _issue_admin_token(conn, admin["id"])
        else:
            user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
            if not user or not check_password_hash(user["password_hash"], current_password):
                return response_error("INVALID_CURRENT_PASSWORD", "当前密码错误", 401)
            conn.execute(
                "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                (generate_password_hash(new_password, method="scrypt"), utcnow(), user["id"]),
            )
            conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user["id"],))
            raw, expires = _issue_token(conn, user["id"])
    return jsonify({"ok": True, "message": "密码修改成功", "token": raw, "expires_at": expires.isoformat(timespec="seconds")})


def _nodes_payload():
    with connect() as conn:
        rows = conn.execute(
            """SELECT n.*,COALESCE(co.sort_order,999999) country_sort_order
               FROM nodes n
               LEFT JOIN country_orders co ON co.country_code=n.country_code
               WHERE n.status='enabled'
               ORDER BY country_sort_order,n.country_code,n.sort_order,n.id"""
        ).fetchall()

    countries = {}
    total = 0
    skipped = 0
    for row in rows:
        try:
            profile = canonical_profile(decrypt_text(current_app, row["config_enc"]))
            if profile.get("schema") != "xvpn.node.v1":
                raise ValueError("invalid node schema")
        except Exception:
            skipped += 1
            continue

        code = row["country_code"]
        countries.setdefault(
            code,
            {
                "country": row["country"],
                "country_code": code,
                "flag_emoji": "".join(chr(127397 + ord(ch)) for ch in code)
                if len(code) == 2 and code.isalpha() and code != "ZZ" else "🌐",
                "sort_order": row["country_sort_order"],
                "nodes": [],
            },
        )
        countries[code]["nodes"].append({
            "id": row["id"],
            "name": row["name"],
            "display_name": row["name"],
            "country": row["country"],
            "country_code": code,
            "region": row["region"],
            "protocol": profile["protocol"],
            "profile": profile,
            "sort_order": row["sort_order"],
        })
        total += 1

    revision_source = json.dumps([
        [int(row["id"]), str(row["updated_at"]), str(row["status"]), int(row["sort_order"])]
        for row in rows
    ], ensure_ascii=False, separators=(",", ":"))
    return {
        "ok": True,
        "schema": "xvpn.nodes.v1",
        "node_schema": "xvpn.node.v1",
        "core": "mihomo",
        "countries": list(countries.values()),
        "total": total,
        "skipped_invalid": skipped,
        "revision": hashlib.sha256(revision_source.encode("utf-8")).hexdigest()[:24],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


@api_bp.get("/nodes")
@bearer_required
def nodes():
    return jsonify(_nodes_payload())


@api_bp.get("/app/bootstrap")
@bearer_required
def app_bootstrap():
    payload = _nodes_payload()
    traffic = None
    if g.user.get("role") == "user":
        with connect() as conn:
            traffic = traffic_summary(conn, g.user["id"], tz_name=current_app.config.get("PANEL_TIMEZONE", "UTC"))
    return jsonify({
        "ok": True,
        "api": "v1",
        "version": APP_VERSION,
        "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registration_enabled": bool(current_app.config.get("REGISTRATION_ENABLED", True)),
        "traffic_reporting": True,
        "traffic_report_interval_seconds": 10,
        "traffic_report_requires_node_id": True,
        "panel_timezone": current_app.config.get("PANEL_TIMEZONE", "UTC"),
        "app_update_api": True,
        "app_update_check_interval_seconds": 43200,
        "core": "mihomo",
        "node_schema": "xvpn.node.v1",
        "user": {"id": g.user["id"], "username": g.user["username"], "status": "active", "role": g.user.get("role", "user")},
        "traffic": traffic,
        "nodes": {
            "schema": payload["schema"],
            "node_schema": payload["node_schema"],
            "core": payload["core"],
            "countries": payload["countries"],
            "total": payload["total"],
            "skipped_invalid": payload["skipped_invalid"],
            "revision": payload["revision"],
            "generated_at": payload["generated_at"],
        },
    })


def _counter_value(data, key):
    value = data.get(key)
    if isinstance(value, bool):
        raise ValueError
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError
    if value < 0 or value > 9_000_000_000_000_000_000:
        raise ValueError
    return value


@api_bp.post("/traffic/report")
@bearer_required
def traffic_report():
    if g.user.get("role") != "user":
        return response_error("TRAFFIC_USER_REQUIRED", "管理员账户不计入用户流量统计", 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return response_error("INVALID_JSON", "请求内容必须是 JSON")

    device_id = str(data.get("device_id", "")).strip()
    session_id = str(data.get("session_id", "")).strip()
    app_version = str(data.get("app_version", "")).strip()[:64]
    if len(device_id) < 8 or len(device_id) > 128:
        return response_error("INVALID_DEVICE_ID", "device_id 长度需为 8-128 位")
    if len(session_id) < 8 or len(session_id) > 128:
        return response_error("INVALID_SESSION_ID", "session_id 长度需为 8-128 位")
    if any(ord(ch) < 32 for ch in device_id + session_id):
        return response_error("INVALID_DEVICE_ID", "设备或会话标识格式无效")

    node_raw = data.get("node_id")
    if isinstance(node_raw, bool):
        return response_error("INVALID_NODE_ID", "node_id 必须是节点列表返回的整数 ID")
    try:
        node_id = int(node_raw)
    except (TypeError, ValueError):
        return response_error("INVALID_NODE_ID", "node_id 必须是节点列表返回的整数 ID")
    if node_id <= 0:
        return response_error("INVALID_NODE_ID", "node_id 必须是节点列表返回的整数 ID")
    try:
        upload_total = _counter_value(data, "upload_total_bytes")
        download_total = _counter_value(data, "download_total_bytes")
    except ValueError:
        return response_error("INVALID_COUNTER", "流量累计值必须是非负整数")

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    day, _ = traffic_period_keys(now, current_app.config.get("PANEL_TIMEZONE", "UTC"))
    stale_before = (now - timedelta(days=180)).isoformat(timespec="seconds")
    baseline_reset = False

    with transaction() as conn:
        node = conn.execute("SELECT id,name,country,region FROM nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            return response_error("INVALID_NODE_ID", "节点不存在，请先刷新节点列表", 400)

        conn.execute("DELETE FROM traffic_session_counters WHERE last_report_at<?", (stale_before,))
        row = conn.execute(
            """SELECT * FROM traffic_session_counters
               WHERE user_id=? AND device_id=? AND session_id=? AND node_id=?""",
            (g.user["id"], device_id, session_id, node_id),
        ).fetchone()
        if not row:
            # A delayed first request must not lose traffic. The session key
            # makes this full cumulative value idempotent on every retry.
            delta_upload = upload_total
            delta_download = download_total
            baseline_reset = True
            conn.execute(
                """INSERT INTO traffic_session_counters(
                       user_id,device_id,session_id,node_id,upload_total_bytes,download_total_bytes,
                       app_version,first_report_at,last_report_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (g.user["id"], device_id, session_id, node_id, upload_total, download_total,
                 app_version, now_iso, now_iso),
            )
        else:
            monotonic = upload_total >= int(row["upload_total_bytes"]) and download_total >= int(row["download_total_bytes"])
            if monotonic:
                delta_upload = upload_total - int(row["upload_total_bytes"])
                delta_download = download_total - int(row["download_total_bytes"])
            else:
                delta_upload = 0
                delta_download = 0
                baseline_reset = True
            conn.execute(
                """UPDATE traffic_session_counters SET
                   upload_total_bytes=MAX(upload_total_bytes,?),
                   download_total_bytes=MAX(download_total_bytes,?),app_version=?,last_report_at=?
                   WHERE user_id=? AND device_id=? AND session_id=? AND node_id=?""",
                (upload_total, download_total, app_version, now_iso,
                 g.user["id"], device_id, session_id, node_id),
            )

        conn.execute(
            """INSERT INTO traffic_device_counters(
                   user_id,device_id,session_id,node_id,upload_total_bytes,download_total_bytes,app_version,last_report_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id,device_id) DO UPDATE SET
                   session_id=excluded.session_id,node_id=excluded.node_id,
                   upload_total_bytes=CASE
                       WHEN traffic_device_counters.session_id=excluded.session_id
                        AND traffic_device_counters.node_id=excluded.node_id
                       THEN MAX(traffic_device_counters.upload_total_bytes,excluded.upload_total_bytes)
                       ELSE excluded.upload_total_bytes END,
                   download_total_bytes=CASE
                       WHEN traffic_device_counters.session_id=excluded.session_id
                        AND traffic_device_counters.node_id=excluded.node_id
                       THEN MAX(traffic_device_counters.download_total_bytes,excluded.download_total_bytes)
                       ELSE excluded.download_total_bytes END,
                   app_version=excluded.app_version,last_report_at=excluded.last_report_at""",
            (g.user["id"], device_id, session_id, node_id, upload_total, download_total, app_version, now_iso),
        )

        conn.execute(
            """INSERT INTO traffic_daily(user_id,day,upload_bytes,download_bytes,report_count,updated_at)
               VALUES(?,?,?,?,1,?)
               ON CONFLICT(user_id,day) DO UPDATE SET
                   upload_bytes=traffic_daily.upload_bytes+excluded.upload_bytes,
                   download_bytes=traffic_daily.download_bytes+excluded.download_bytes,
                   report_count=traffic_daily.report_count+1,
                   updated_at=excluded.updated_at""",
            (g.user["id"], day, delta_upload, delta_download, now_iso),
        )
        conn.execute(
            """INSERT INTO traffic_node_daily(
                   user_id,node_id,day,node_name,country,region,upload_bytes,download_bytes,report_count,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,1,?)
               ON CONFLICT(user_id,node_id,day) DO UPDATE SET
                   node_name=excluded.node_name,
                   country=excluded.country,
                   region=excluded.region,
                   upload_bytes=traffic_node_daily.upload_bytes+excluded.upload_bytes,
                   download_bytes=traffic_node_daily.download_bytes+excluded.download_bytes,
                   report_count=traffic_node_daily.report_count+1,
                   updated_at=excluded.updated_at""",
            (
                g.user["id"], node_id, day, node["name"], node["country"], node["region"],
                delta_upload, delta_download, now_iso,
            ),
        )
        summary = traffic_summary(conn, g.user["id"], now=now, tz_name=current_app.config.get("PANEL_TIMEZONE", "UTC"))

    return jsonify({
        "ok": True,
        "accepted": True,
        "baseline_reset": baseline_reset,
        "server_time": now_iso,
        "node": {"id": node_id, "name": node["name"]},
        "delta": {"upload_bytes": delta_upload, "download_bytes": delta_download},
        "traffic": summary,
    })


@api_bp.get("/traffic/summary")
@bearer_required
def traffic_summary_api():
    if g.user.get("role") != "user":
        return response_error("TRAFFIC_USER_REQUIRED", "管理员账户不计入用户流量统计", 403)
    with connect() as conn:
        summary = traffic_summary(conn, g.user["id"], tz_name=current_app.config.get("PANEL_TIMEZONE", "UTC"))
    return jsonify({"ok": True, "traffic": summary})
