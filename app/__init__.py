import os
import re
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import bootstrap_admin, connect, init_db
from .crypto import ensure_crypto_ready
from .admin import admin_bp
from .admin_v1 import install_overrides
from .api import api_bp
from .settings_store import apply_settings, initialize_from_env


def _admin_path(app):
    value = "admin"
    try:
        with connect(app) as conn:
            row = conn.execute("SELECT value FROM system_settings WHERE key='admin_path'").fetchone()
            if row and row[0]:
                value = str(row[0]).strip().strip("/")
    except Exception:
        pass
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,47}", value):
        return "admin"
    if value.lower() in {"api", "static", "assets", "health"}:
        return "admin"
    return value


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    app.json.ensure_ascii = False
    app.config["PANEL_NAME"] = os.environ.get("PANEL_NAME", "XVPN Panel")
    app.config["PANEL_SUBTITLE"] = os.environ.get("PANEL_SUBTITLE", "私人访问控制台")
    app.config["TOKEN_DAYS"] = int(os.environ.get("TOKEN_DAYS", "30"))
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
    app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", "./data/panel.db")
    app.config["BACKUP_DIR"] = os.environ.get("BACKUP_DIR", "/var/backups/xvpn-panel")
    app.config["FERNET_KEY"] = os.environ.get("FERNET_KEY", "")
    app.config["ANDROID_UPDATE_REPOSITORY"] = os.environ.get(
        "XVPN_ANDROID_REPOSITORY", "kkx999/XVPN-Android"
    ).strip() or "kkx999/XVPN-Android"
    app.config["ADMIN_ALLOWED_IPS"] = {
        x.strip() for x in os.environ.get("ADMIN_ALLOWED_IPS", "").split(",") if x.strip()
    }

    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    ensure_crypto_ready(app)
    init_db(app)
    initialize_from_env(app)
    apply_settings(app)
    bootstrap_admin(app)

    path = _admin_path(app)
    app.config["ADMIN_PATH"] = path
    app.register_blueprint(admin_bp, url_prefix=f"/{path}")
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    install_overrides(app)

    @app.get("/")
    def root():
        return {
            "service": app.config["PANEL_NAME"],
            "status": "ok",
            "core": "mihomo",
            "node_schema": "xvpn.node.v1",
        }

    return app
