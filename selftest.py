#!/usr/bin/env python3
import base64
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def vmess_sample():
    obj = {
        "v": "2",
        "ps": "测试VMess",
        "add": "vm.example.com",
        "port": "443",
        "id": "11111111-1111-4111-8111-111111111111",
        "aid": "0",
        "scy": "auto",
        "net": "ws",
        "host": "vm.example.com",
        "path": "/ws",
        "tls": "tls",
        "sni": "vm.example.com",
    }
    raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return "vmess://" + raw


def main():
    os.environ["XVPN_DISABLE_SELF_RELOAD"] = "1"
    os.environ["SECRET_KEY"] = "selftest-secret-key"
    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
    os.environ["ADMIN_PASSWORD"] = "SelfTestPassword123"
    os.environ["TRUST_PROXY"] = "0"
    os.environ["COOKIE_SECURE"] = "0"

    with tempfile.TemporaryDirectory(prefix="xvpn-panel-selftest-") as tmp:
        os.environ["DATABASE_PATH"] = str(Path(tmp) / "panel.db")

        from app import create_app
        from app.admin_v1 import _canonical
        from app.crypto import encrypt_text
        from app.db import connect, utcnow
        from app.settings_store import set_settings

        samples = [
            "vless://11111111-1111-4111-8111-111111111111@vless.example.com:443?security=reality&sni=www.example.com&fp=chrome&pbk=PUBLIC_KEY_TEST&sid=abcd&flow=xtls-rprx-vision#VLESS-Reality",
            "trojan://strong-password@trojan.example.com:443?sni=trojan.example.com#Trojan",
            "hysteria2://hy2-password@hy2.example.com:443?sni=hy2.example.com&obfs=salamander&obfs-password=abc#HY2",
            "tuic://11111111-1111-4111-8111-111111111111:tuic-password@tuic.example.com:443?sni=tuic.example.com&congestion_control=bbr#TUIC",
            "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@ss.example.com:8388#SS",
            vmess_sample(),
        ]
        protocols = []
        canonical_samples = []
        for raw in samples:
            profile, canonical = _canonical(raw)
            require(profile["schema"] == "xvpn.node.v1", "node schema mismatch")
            require(profile["server"] and 1 <= int(profile["port"]) <= 65535, "node endpoint invalid")
            require(json.loads(canonical)["protocol"] == profile["protocol"], "canonical JSON mismatch")
            protocols.append(profile["protocol"])
            canonical_samples.append(canonical)
        require(protocols == ["vless", "trojan", "hysteria2", "tuic", "shadowsocks", "vmess"], "protocol parser mismatch")

        try:
            _canonical('{"schema":"xvpn.node.v1","protocol":"vless","server":"x.example.com","port":443,"auth":{},"tls":{"enabled":false},"transport":{"type":"tcp"},"options":{}}')
        except ValueError:
            pass
        else:
            raise AssertionError("invalid canonical VLESS was accepted")

        app = create_app()
        app.testing = True
        client = app.test_client()
        health = client.get("/api/v1/health")
        require(health.status_code == 200, "health endpoint failed")
        health_json = health.get_json() or {}
        require(health_json.get("version") == "1.0.0", "panel version mismatch")
        require(health_json.get("core") == "mihomo", "health core mismatch")
        require(client.get("/admin/login").status_code == 200, "default admin path failed")
        require(client.get("/not-admin/login").status_code == 404, "unexpected admin alias exposed")

        with app.app_context():
            now = utcnow()
            with connect() as conn:
                conn.execute(
                    "INSERT INTO invites(code,status,max_uses,use_count,created_at) VALUES(?,?,?,?,?)",
                    ("SELFTEST", "active", 1, 0, now),
                )
                conn.execute(
                    "INSERT INTO country_orders(country_code,sort_order,updated_at) VALUES(?,?,?)",
                    ("HK", 10, now),
                )
                conn.execute(
                    """INSERT INTO nodes(name,original_name,country,country_code,region,protocol,config_enc,sort_order,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "香港01", "VLESS-Reality", "香港", "HK", "", "vless",
                        encrypt_text(app, canonical_samples[0]), 100, "enabled", now, now,
                    ),
                )
                conn.commit()

        register = client.post("/api/v1/register", json={
            "invite_code": "SELFTEST",
            "username": "selftest-user",
            "password": "UserPassword123",
        })
        require(register.status_code == 201 and (register.get_json() or {}).get("ok") is True, "register API failed")

        login = client.post("/api/v1/login", json={"username": "selftest-user", "password": "UserPassword123"})
        login_json = login.get_json() or {}
        require(login.status_code == 200 and login_json.get("token"), "login API failed")
        auth = {"Authorization": "Bearer " + login_json["token"]}

        nodes = client.get("/api/v1/nodes", headers=auth)
        nodes_json = nodes.get_json() or {}
        require(nodes.status_code == 200 and nodes_json.get("core") == "mihomo", "nodes API failed")
        require(nodes_json.get("node_schema") == "xvpn.node.v1" and nodes_json.get("total") == 1, "nodes schema/count mismatch")
        node = nodes_json["countries"][0]["nodes"][0]
        require(node.get("profile", {}).get("protocol") == "vless", "node profile missing")
        require("config" not in node, "legacy raw config field leaked")

        bootstrap = client.get("/api/v1/app/bootstrap", headers=auth)
        bootstrap_json = bootstrap.get_json() or {}
        require(bootstrap.status_code == 200 and bootstrap_json.get("core") == "mihomo", "bootstrap API failed")
        require((bootstrap_json.get("nodes") or {}).get("node_schema") == "xvpn.node.v1", "bootstrap node schema mismatch")

        report1 = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01",
            "session_id": "session-selftest-01",
            "node_id": node["id"],
            "upload_total_bytes": 100,
            "download_total_bytes": 200,
            "app_version": "1.0.0",
        })
        r1 = report1.get_json() or {}
        require(report1.status_code == 200 and r1.get("baseline_reset") is True, "traffic baseline failed")

        report2 = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01",
            "session_id": "session-selftest-01",
            "node_id": node["id"],
            "upload_total_bytes": 150,
            "download_total_bytes": 260,
            "app_version": "1.0.0",
        })
        r2 = report2.get_json() or {}
        require(report2.status_code == 200, "traffic delta report failed")
        require((r2.get("delta") or {}).get("upload_bytes") == 50, "traffic upload delta mismatch")
        require((r2.get("delta") or {}).get("download_bytes") == 60, "traffic download delta mismatch")

        with app.app_context():
            set_settings({"admin_path": "manage-xvpn"})

        app2 = create_app()
        app2.testing = True
        client2 = app2.test_client()
        require(client2.get("/manage-xvpn/login").status_code == 200, "custom admin path failed")
        require(client2.get("/admin/login").status_code == 404, "old admin path still exposed")
        root = client2.get("/").get_json() or {}
        require(root.get("core") == "mihomo" and root.get("node_schema") == "xvpn.node.v1", "root capability metadata mismatch")

    print("XVPN Panel self-test: OK")


if __name__ == "__main__":
    main()
