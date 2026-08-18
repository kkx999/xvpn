import os
from pathlib import Path
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import init_db, bootstrap_admin
from .crypto import ensure_crypto_ready
from .admin import admin_bp
from .api import api_bp
from .settings_store import initialize_from_env, apply_settings


def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
    # Keep Chinese API messages readable in curl/browser output.
    app.json.ensure_ascii = False
    app.config["PANEL_NAME"] = os.environ.get("PANEL_NAME", "XVPN Panel")
    app.config["PANEL_SUBTITLE"] = os.environ.get("PANEL_SUBTITLE", "私人访问控制台")
    app.config["TOKEN_DAYS"] = int(os.environ.get("TOKEN_DAYS", "30"))
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
    app.config["DATABASE_PATH"] = os.environ.get("DATABASE_PATH", "./data/panel.db")
    app.config["FERNET_KEY"] = os.environ.get("FERNET_KEY", "")
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

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api/v1")

    @app.get("/")
    def root():
        return {"service": app.config["PANEL_NAME"], "status": "ok"}

    return app
