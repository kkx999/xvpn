#!/usr/bin/env python3
import base64
import json
import os
import tempfile
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from flask import Flask


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


def expect_invalid(parser, raw, label):
    try:
        parser(raw)
    except ValueError:
        return
    raise AssertionError(f"invalid node was accepted: {label}")


def main():
    os.environ["XVPN_DISABLE_SELF_RELOAD"] = "1"
    os.environ["SECRET_KEY"] = "selftest-secret-key"
    os.environ["FERNET_KEY"] = Fernet.generate_key().decode()
    os.environ["ADMIN_PASSWORD"] = "SelfTestPassword123"
    os.environ["TRUST_PROXY"] = "0"
    os.environ["COOKIE_SECURE"] = "0"

    with tempfile.TemporaryDirectory(prefix="xvpn-panel-selftest-") as tmp:
        root = Path(tmp)
        os.environ["DATABASE_PATH"] = str(root / "panel.db")
        os.environ["BACKUP_DIR"] = str(root / "backups")

        from app import create_app
        from app.admin_v1 import _canonical
        from app.backup_manager import create_backup, restore_backup
        from app.crypto import encrypt_text
        from app.db import bootstrap_admin, connect, init_db, utcnow
        from app.settings_store import get_settings, set_settings
        from app.ip_classifier import classify_payload, normalize_public_ip

        # Concurrent first boot must create exactly one administrator.
        race_app = Flask("xvpn-bootstrap-selftest")
        race_app.config["DATABASE_PATH"] = str(root / "bootstrap-race.db")
        init_db(race_app)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(bootstrap_admin, race_app) for _ in range(8)]
            for future in futures:
                future.result()
        with connect(race_app) as conn:
            admin_count = int(conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0])
        require(admin_count == 1, "concurrent admin bootstrap created duplicate rows")

        samples = [
            "vless://11111111-1111-4111-8111-111111111111@vless.example.com:443?security=reality&sni=www.example.com&fp=chrome&pbk=PUBLIC_KEY_TEST&sid=abcd&flow=xtls-rprx-vision#VLESS-Reality",
            "trojan://strong-password@trojan.example.com:443?sni=trojan.example.com#Trojan",
            "hysteria2://hy2-password@hy2.example.com:443?sni=hy2.example.com&obfs=salamander&obfs-password=abc#HY2",
            "tuic://11111111-1111-4111-8111-111111111111:tuic-password@tuic.example.com:443?sni=tuic.example.com&congestion_control=bbr#TUIC",
            "ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ=@ss.example.com:8388#SS",
            vmess_sample(),
            "anytls://anytls-password@anytls.example.com:443?sni=anytls.example.com#AnyTLS",
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
        require(
            protocols == ["vless", "trojan", "hysteria2", "tuic", "shadowsocks", "vmess", "anytls"],
            "protocol parser mismatch",
        )

        classified = classify_payload({
            "ip": "8.8.8.8",
            "company": {"name": "Google Cloud", "type": "hosting"},
            "location": {"country_code": "US"},
            "is_datacenter": True,
        }, "8.8.8.8", keyed=True)
        require(classified["classification"] == "datacenter", "keyed datacenter classification failed")
        require(classified["country_code"] == "US", "classification country code failed")
        conservative = classify_payload({
            "ip": "8.8.4.4",
            "company": {"name": "Example Business", "type": "business"},
            "is_datacenter": False,
        }, "8.8.4.4", keyed=True)
        require(conservative["classification"] == "unknown", "non-datacenter was mislabeled residential")
        require(normalize_public_ip("2606:4700:4700::1111") == "2606:4700:4700::1111", "IPv6 normalization failed")
        try:
            normalize_public_ip("127.0.0.1")
        except ValueError:
            pass
        else:
            raise AssertionError("private IP classification was accepted")

        expect_invalid(
            _canonical,
            '{"schema":"xvpn.node.v1","protocol":"vless","server":"x.example.com","port":443,"auth":{},"tls":{"enabled":false},"transport":{"type":"tcp"},"options":{}}',
            "missing VLESS UUID",
        )
        expect_invalid(
            _canonical,
            "vless://not-a-uuid@x.example.com:443?security=tls&sni=x.example.com#bad-uuid",
            "malformed VLESS UUID",
        )
        expect_invalid(
            _canonical,
            "vless://11111111-1111-4111-8111-111111111111@x.example.com:443?security=reality&sni=x.example.com#missing-pbk",
            "Reality without public key",
        )
        expect_invalid(
            _canonical,
            '{"schema":"xvpn.node.v1","protocol":"hysteria2","server":"x.example.com","port":443,"auth":{"password":"x"},"tls":{"enabled":false},"transport":{"type":"tcp"},"options":{}}',
            "Hysteria2 without TLS",
        )

        app = create_app()
        app.testing = True
        client = app.test_client()
        health = client.get("/api/v1/health")
        require(health.status_code == 200, "health endpoint failed")
        health_json = health.get_json() or {}
        require(health_json.get("version") == "1.1", "panel version mismatch")
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
        require(bootstrap_json.get("traffic_report_interval_seconds") == 10, "traffic interval mismatch")
        require((bootstrap_json.get("nodes") or {}).get("revision"), "node revision missing")
        require(bootstrap_json.get("exit_ip_classification") is True, "IP classification capability missing")

        with patch("app.api.classify_ip", return_value={
            "ok": True, "ip": "8.8.8.8", "classification": "datacenter",
            "type_label": "机房 IP", "provider": "Google LLC", "country_code": "US",
            "confidence": "high", "source": "selftest", "cached": False,
        }):
            ip_result = client.post("/api/v1/ip/classify", headers=auth, json={"ip": "8.8.8.8"})
        require(ip_result.status_code == 200, "IP classification API failed")
        require((ip_result.get_json() or {}).get("classification") == "datacenter", "IP classification response mismatch")

        # Minimum-version policy applies to protected APIs, not only /app/update.
        with app.app_context():
            snapshot = {
                "repository": "kkx999/XVPN-Android", "tag": "v1.0",
                "version_name": "1.0", "version_code": 10101,
                "release_name": "XVPN v1.0", "release_notes": "test",
                "release_url": "https://github.com/kkx999/XVPN-Android/releases/tag/v1.0",
                "apk_name": "XVPN-v1.0.apk",
                "apk_url": "https://github.com/kkx999/XVPN-Android/releases/download/v1.0/XVPN-v1.0.apk",
                "apk_size": 123, "sha256": "a" * 64,
            }
            set_settings({
                "app_update_min_version_code": "10101",
                "app_update_last_checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app_update_last_snapshot_json": json.dumps(snapshot, separators=(",", ":")),
                "app_update_last_stale": "0",
            })
        old_headers = dict(auth)
        old_headers.update({"X-XVPN-Version-Name": "1.0.0", "X-XVPN-Version-Code": "10000"})
        blocked = client.get("/api/v1/app/bootstrap", headers=old_headers)
        require(blocked.status_code == 426, "minimum version did not block protected API")
        require((blocked.get_json() or {}).get("code") == "APP_VERSION_UNSUPPORTED", "version block payload mismatch")
        with app.app_context():
            set_settings({"app_update_min_version_code": "0"})

        report1 = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01",
            "session_id": "session-selftest-01",
            "node_id": node["id"],
            "upload_total_bytes": 100,
            "download_total_bytes": 200,
            "app_version": "1.0",
        })
        r1 = report1.get_json() or {}
        require(report1.status_code == 200 and r1.get("baseline_reset") is True, "traffic first report failed")
        require((r1.get("delta") or {}).get("upload_bytes") == 100, "traffic first upload was lost")
        require((r1.get("delta") or {}).get("download_bytes") == 200, "traffic first download was lost")

        report2 = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01",
            "session_id": "session-selftest-01",
            "node_id": node["id"],
            "upload_total_bytes": 150,
            "download_total_bytes": 260,
            "app_version": "1.0",
        })
        r2 = report2.get_json() or {}
        require(report2.status_code == 200, "traffic delta report failed")
        require((r2.get("delta") or {}).get("upload_bytes") == 50, "traffic upload delta mismatch")
        require((r2.get("delta") or {}).get("download_bytes") == 60, "traffic download delta mismatch")

        duplicate = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01", "session_id": "session-selftest-01",
            "node_id": node["id"], "upload_total_bytes": 150,
            "download_total_bytes": 260, "app_version": "1.0",
        })
        require((duplicate.get_json() or {}).get("delta") == {"upload_bytes": 0, "download_bytes": 0},
                "duplicate traffic report was counted twice")

        delayed_new_session = client.post("/api/v1/traffic/report", headers=auth, json={
            "device_id": "device-selftest-01", "session_id": "session-selftest-02",
            "node_id": node["id"], "upload_total_bytes": 25,
            "download_total_bytes": 40, "app_version": "1.0",
        })
        delayed_json = delayed_new_session.get_json() or {}
        require((delayed_json.get("delta") or {}).get("upload_bytes") == 25,
                "delayed first report lost upload traffic")
        require((delayed_json.get("delta") or {}).get("download_bytes") == 40,
                "delayed first report lost download traffic")

        # Backup before changing admin_path, then restore after the change. Restore must
        # keep the current access path and must not revive the App bearer token.
        with app.app_context():
            archive = create_backup("manual")
            require(archive.parent == root / "backups", "backup directory mismatch")
            require(archive.is_file() and archive.stat().st_size > 0, "backup archive missing")
            set_settings({"admin_path": "manage-xvpn"})
            restore_backup(archive)
            require(get_settings().get("admin_path") == "manage-xvpn", "backup restore changed admin path")

        app2 = create_app()
        app2.testing = True
        client2 = app2.test_client()
        require(client2.get("/manage-xvpn/login").status_code == 200, "custom admin path failed")
        require(client2.get("/admin/login").status_code == 404, "old admin path still exposed")
        require(client2.get("/api/v1/nodes", headers=auth).status_code == 401, "backup restore revived old App token")
        root_json = client2.get("/").get_json() or {}
        require(root_json.get("core") == "mihomo" and root_json.get("node_schema") == "xvpn.node.v1", "root capability metadata mismatch")

    print("XVPN Panel self-test: OK")


if __name__ == "__main__":
    main()
