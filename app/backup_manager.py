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

BACKUP_FORMAT = 2
REQUIRED_TABLES = {"admins", "users", "invites", "nodes", "api_tokens", "system_settings"}


def backup_dir():
    path = Path(current_app.config["BACKUP_DIR"]).resolve()
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

        _scrub_auth_state(tmp_db)
        sha = hashlib.sha256(tmp_db.read_bytes()).hexdigest()
        manifest = {
            "format": BACKUP_FORMAT,
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
            zf.writestr("recovery.key", current_app.config["FERNET_KEY"])
    return target


def _kind_from_name(name):
    for kind in ("manual", "auto", "pre-restore"):
        if name.startswith(f"xvpn-panel-{kind}-"):
            return kind
    return "other"


def list_backups():
    rows = []
    for path in sorted(backup_dir().glob("xvpn-panel-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
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
        for row_id, value in conn.execute("SELECT id,config_enc FROM nodes").fetchall():
            try:
                plain = old.decrypt(value.encode()).decode()
            except InvalidToken as exc:
                raise ValueError("备份中的节点配置无法使用恢复密钥解密") from exc
            conn.execute("UPDATE nodes SET config_enc=? WHERE id=?", (new.encrypt(plain.encode()).decode(), row_id))

        row = conn.execute("SELECT value FROM system_settings WHERE key='telegram_bot_token_enc'").fetchone()
        if row and row[0]:
            try:
                plain = old.decrypt(row[0].encode()).decode()
                conn.execute(
                    "UPDATE system_settings SET value=? WHERE key='telegram_bot_token_enc'",
                    (new.encrypt(plain.encode()).decode(),),
                )
            except InvalidToken:
                conn.execute("UPDATE system_settings SET value='' WHERE key='telegram_bot_token_enc'")
                conn.execute("UPDATE system_settings SET value='0' WHERE key='telegram_enabled'")
        conn.commit()
    finally:
        conn.close()


def _current_admin_path():
    try:
        with connect() as conn:
            row = conn.execute("SELECT value FROM system_settings WHERE key='admin_path'").fetchone()
        value = str(row[0] if row else "admin").strip().strip("/")
        return value or "admin"
    except Exception:
        return "admin"


def restore_backup(archive_path):
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise ValueError("备份文件不存在")

    preserved_admin_path = _current_admin_path()

    with tempfile.TemporaryDirectory(prefix="xvpn-panel-restore-") as tmp:
        tmp_dir = Path(tmp)
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                names = set(zf.namelist())
                if {"panel.db", "manifest.json", "recovery.key"} - names:
                    raise ValueError("不是有效的 XVPN Panel v1 备份")
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                if int(manifest.get("format", 0)) != BACKUP_FORMAT or manifest.get("service") != "XVPN Panel":
                    raise ValueError("备份格式版本不受支持")
                zf.extract("panel.db", tmp_dir)
                backup_key = zf.read("recovery.key").decode("utf-8").strip()
                try:
                    Fernet(backup_key.encode())
                except Exception as exc:
                    raise ValueError("备份恢复密钥格式无效") from exc
        except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("备份压缩包损坏或格式不正确") from exc

        src_db = tmp_dir / "panel.db"
        _validate_db(src_db)
        expected = str(manifest.get("database_sha256") or "").lower()
        actual = hashlib.sha256(src_db.read_bytes()).hexdigest()
        if not expected or expected != actual:
            raise ValueError("备份数据库校验值不匹配")

        _scrub_auth_state(src_db)
        _reencrypt_db(src_db, backup_key, current_app.config["FERNET_KEY"])
        _validate_db(src_db)
        create_backup("pre-restore")

        source = sqlite3.connect(src_db)
        dest = sqlite3.connect(current_app.config["DATABASE_PATH"])
        try:
            source.backup(dest)
            dest.execute(
                """INSERT INTO system_settings(key,value,updated_at) VALUES('admin_path',?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                (preserved_admin_path, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            for table in ("api_tokens", "admin_api_tokens", "auth_rate_limits"):
                try:
                    dest.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass
            dest.commit()
        finally:
            source.close()
            dest.close()

    init_db(current_app)
    apply_settings(current_app)
