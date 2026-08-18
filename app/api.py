import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from .crypto import decrypt_text
from .db import connect, transaction, utcnow
from .version import APP_VERSION

api_bp = Blueprint("api", __name__)


@api_bp.after_request
def api_security_headers(response):
    # Tokens and node configs must never be cached by browsers/proxies.
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
            conn.execute("UPDATE auth_rate_limits SET blocked_until=? WHERE rate_key=?", (blocked.isoformat(timespec="seconds"), key))
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
            conn.execute("UPDATE auth_rate_limits SET attempts=?,blocked_until=? WHERE rate_key=?", (attempts, blocked_until, key))
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


def bearer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return response_error("UNAUTHORIZED", "请先登录", 401)
        raw = auth[7:].strip()
        now = datetime.now(timezone.utc)
        with connect() as conn:
            row = conn.execute(
                """SELECT api_tokens.*, users.username, users.status user_status
                   FROM api_tokens JOIN users ON users.id=api_tokens.user_id
                   WHERE token_hash=?""",
                (token_hash(raw),),
            ).fetchone()
            if not row:
                return response_error("UNAUTHORIZED", "登录状态已失效", 401)
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
        g.user = {"id": row["user_id"], "username": row["username"], "token": raw}
        return view(*args, **kwargs)
    return wrapped


@api_bp.get("")
@api_bp.get("/")
def api_index():
    return {
        "ok": True,
        "service": current_app.config["PANEL_NAME"],
        "api": "v1",
        "version": APP_VERSION,
        "registration_enabled": bool(current_app.config.get("REGISTRATION_ENABLED", True)),
        "token_days": int(current_app.config.get("TOKEN_DAYS", 30)),
        "app_api_ready": True,
    }


@api_bp.get("/health")
def health():
    return {"ok": True, "service": current_app.config["PANEL_NAME"], "version": APP_VERSION}


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
        if (
            not invite
            or invite["status"] != "active"
            or int(invite["use_count"]) >= int(invite["max_uses"])
        ):
            _rate_fail("register", "invite", max_attempts=20, window_seconds=900, block_seconds=900, conn=conn)
            return response_error("INVALID_INVITE", "邀请码无效、已作废或使用次数已用完", 403)
        if conn.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone():
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
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            _rate_fail("login", username, max_attempts=10, window_seconds=900, block_seconds=900)
            return response_error("INVALID_CREDENTIALS", "用户名或密码错误", 401)
        if user["status"] != "active":
            return response_error("ACCOUNT_DISABLED", "账户已停用", 403)
        raw, expires = _issue_token(conn, user["id"])
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (utcnow(), user["id"]))
        conn.commit()
    _rate_reset("login", username)
    return jsonify(
        {
            "ok": True,
            "token": raw,
            "expires_at": expires.isoformat(timespec="seconds"),
            "user": {"id": user["id"], "username": user["username"]},
        }
    )


@api_bp.post("/logout")
@bearer_required
def logout():
    with connect() as conn:
        conn.execute("DELETE FROM api_tokens WHERE token_hash=?", (token_hash(g.user["token"]),))
        conn.commit()
    return {"ok": True}


@api_bp.get("/me")
@bearer_required
def me():
    return {"ok": True, "user": {"id": g.user["id"], "username": g.user["username"], "status": "active"}}


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
        user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
        if not user or not check_password_hash(user["password_hash"], current_password):
            return response_error("INVALID_CURRENT_PASSWORD", "当前密码错误", 401)
        conn.execute(
            "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
            (generate_password_hash(new_password, method="scrypt"), utcnow(), user["id"]),
        )
        conn.execute("DELETE FROM api_tokens WHERE user_id=?", (user["id"],))
        raw, expires = _issue_token(conn, user["id"])
    return jsonify(
        {
            "ok": True,
            "message": "密码修改成功",
            "token": raw,
            "expires_at": expires.isoformat(timespec="seconds"),
        }
    )


def _nodes_payload():
    with connect() as conn:
        rows = conn.execute(
            """SELECT n.*, COALESCE(co.sort_order, 999999) country_sort_order
               FROM nodes n
               LEFT JOIN country_orders co ON co.country_code=n.country_code
               WHERE n.status='enabled'
               ORDER BY country_sort_order, n.country_code, n.sort_order, n.id"""
        ).fetchall()
    countries = {}
    for row in rows:
        key = row["country_code"]
        countries.setdefault(
            key,
            {
                "country": row["country"],
                "country_code": key,
                "flag_emoji": "".join(chr(127397 + ord(ch)) for ch in key) if len(key) == 2 and key.isalpha() and key != "ZZ" else "🌐",
                "sort_order": row["country_sort_order"],
                "nodes": [],
            },
        )
        countries[key]["nodes"].append(
            {
                "id": row["id"],
                "name": row["name"],
                "display_name": row["name"],
                "country": row["country"],
                "country_code": row["country_code"],
                "region": row["region"],
                "protocol": row["protocol"],
                "config": decrypt_text(current_app, row["config_enc"]),
                "sort_order": row["sort_order"],
            }
        )
    return {"ok": True, "countries": list(countries.values()), "total": len(rows)}



@api_bp.get("/app/bootstrap")
@bearer_required
def app_bootstrap():
    payload = _nodes_payload()
    return jsonify({
        "ok": True,
        "api": "v1",
        "version": APP_VERSION,
        "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "registration_enabled": bool(current_app.config.get("REGISTRATION_ENABLED", True)),
        "user": {"id": g.user["id"], "username": g.user["username"], "status": "active"},
        "nodes": {"countries": payload["countries"], "total": payload["total"]},
    })



@api_bp.get("/nodes")
@bearer_required
def nodes():
    return jsonify(_nodes_payload())
