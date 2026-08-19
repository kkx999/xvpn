import hashlib
import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from .db import connect, init_db
from .settings_store import apply_settings
from .version import APP_VERSION

REQUIRED_TABLES = {"admins", "users", "invites", "nodes", "api_tokens"}


def backup_dir():
    path = Path(current_app.config["DATABASE_PATH"]).resolve().parent / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key_fingerprint(key=None):
    key = key or current_app.config["FERNET_KEY"]
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _safe_name(name: str):
    name = Path(name or "").name
    if not name.endswith(".zip") or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in name):
        raise ValueError("invalid backup name")
    return name




def _scrub_auth_state(path: Path):
    """Remove revocable authentication/runtime state from a backup database."""
    conn = sqlite3.connect(path)
    try:
        for table in ("api_tokens", "admin_api_tokens", "auth_rate_limits"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                # Older backups may not have every table yet.
                pass
        conn.commit()
    finally:
        conn.close()


def create_backup(kind="manual"):
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    filename = f"xvpn-panel-{kind}-{stamp}.zip"
    target = backup_dir() / filename

    with tempfile.TemporaryDirectory(prefix="xvpn-panel-backup-") as tmp:
        tmp_db = Path(tmp) / "panel.db"
        src = connect()
        dst = sqlite3.connect(tmp_db)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
            src.close()
        # Authentication sessions are deliberately excluded. User App tokens,
        # administrator App tokens and brute-force state must never travel with backups.
        _scrub_auth_state(tmp_db)
        sha = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
        manifest = {
            "format": 2,
            "service": "XVPN Panel",
            "version": APP_VERSION,
            "created_at": now.isoformat(timespec="seconds"),
            "kind": kind,
            "database_sha256": sha,
            "fernet_fingerprint": _key_fingerprint(),
            "portable": True,
            "note": "备份包含恢复节点加密数据所需的恢复密钥；请像密码一样妥善保管此 ZIP。",
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, "panel.db")
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            # Including the key makes the archive portable. Restore re-encrypts sensitive
            # fields with the destination instance key, so /etc/xvpn-panel.env need not match.
            zf.writestr("recovery.key", current_app.config["FERNET_KEY"])
    return target


def _kind_from_name(name):
    for kind in ("manual", "auto", "pre-restore"):
        if name.startswith(f"xvpn-panel-{kind}-") or name.startswith(f"vpn-panel-{kind}-"):
            return kind
    return "other"


def list_backups():
    rows = []
    paths = set(backup_dir().glob("xvpn-panel-*.zip")) | set(backup_dir().glob("vpn-panel-*.zip"))
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        st = path.stat()
        rows.append(
            {
                "name": path.name,
                "size": st.st_size,
                "created_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "kind": _kind_from_name(path.name),
            }
        )
    return rows


def prune_auto_backups(keep=7):
    auto = [x for x in list_backups() if x["kind"] == "auto"]
    for row in auto[keep:]:
        try:
            get_backup_path(row["name"]).unlink(missing_ok=True)
        except Exception:
            pass


def get_backup_path(name):
    return backup_dir() / _safe_name(name)


def delete_backup(name):
    path = get_backup_path(name)
    if path.exists():
        path.unlink()


def _validate_db(path: Path):
    conn = sqlite3.connect(path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("数据库完整性检查失败")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise ValueError("备份缺少必要数据表")
    finally:
        conn.close()


def _reencrypt_db(path: Path, old_key: str, new_key: str):
    if old_key == new_key:
        return
    old = Fernet(old_key.encode())
    new = Fernet(new_key.encode())
    conn = sqlite3.connect(path)
    try:
        # Node configurations.
        for row_id, value in conn.execute("SELECT id,config_enc FROM nodes").fetchall():
            try:
                plain = old.decrypt(value.encode()).decode()
            except InvalidToken as exc:
                raise ValueError("备份中的节点配置无法使用恢复密钥解密") from exc
            conn.execute("UPDATE nodes SET config_enc=? WHERE id=?", (new.encrypt(plain.encode()).decode(), row_id))
        # Telegram Bot Token is also Fernet protected in system settings.
        row = conn.execute("SELECT value FROM system_settings WHERE key='telegram_bot_token_enc'").fetchone()
        if row and row[0]:
            try:
                plain = old.decrypt(row[0].encode()).decode()
                conn.execute(
                    "UPDATE system_settings SET value=? WHERE key='telegram_bot_token_enc'",
                    (new.encrypt(plain.encode()).decode(),),
                )
            except InvalidToken:
                # Older/manual databases may not have a valid encrypted token; clear it
                # instead of making the whole restore unusable.
                conn.execute("UPDATE system_settings SET value='' WHERE key='telegram_bot_token_enc'")
                conn.execute("UPDATE system_settings SET value='0' WHERE key='telegram_enabled'")
        conn.commit()
    finally:
        conn.close()


def restore_backup(archive_path):
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise ValueError("备份文件不存在")
    with tempfile.TemporaryDirectory(prefix="xvpn-panel-restore-") as tmp:
        tmp_dir = Path(tmp)
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                names = set(zf.namelist())
                if "panel.db" not in names or "manifest.json" not in names:
                    raise ValueError("不是有效的 XVPN Panel 备份")
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                fmt = int(manifest.get("format", 1))
                zf.extract("panel.db", tmp_dir)
                if fmt >= 2 and "recovery.key" in names:
                    backup_key = zf.read("recovery.key").decode("utf-8").strip()
                    try:
                        Fernet(backup_key.encode())
                    except Exception as exc:
                        raise ValueError("备份恢复密钥格式无效") from exc
                else:
                    # Backward compatibility with older format-1 archives: they can only be restored
                    # when the destination kept the same Fernet key.
                    if manifest.get("fernet_fingerprint") != _key_fingerprint():
                        raise ValueError("这是旧格式备份且加密密钥不匹配；请在原实例恢复，或先使用当前版本重新创建备份")
                    backup_key = current_app.config["FERNET_KEY"]
        except (zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise ValueError("备份压缩包损坏或格式不正确") from exc

        src_db = tmp_dir / "panel.db"
        _validate_db(src_db)
        expected = manifest.get("database_sha256")
        if expected and hashlib.sha256(src_db.read_bytes()).hexdigest() != expected:
            raise ValueError("备份数据库校验值不匹配")

        # Old archives may still contain App tokens. Scrub again during restore so
        # restoring any compatible backup cannot revive an old user/admin App login.
        _scrub_auth_state(src_db)
        _reencrypt_db(src_db, backup_key, current_app.config["FERNET_KEY"])
        _validate_db(src_db)
        create_backup("pre-restore")

        source = sqlite3.connect(src_db)
        dest = sqlite3.connect(current_app.config["DATABASE_PATH"])
        try:
            source.backup(dest)
            dest.commit()
        finally:
            source.close()
            dest.close()

    init_db(current_app)
    apply_settings(current_app)
