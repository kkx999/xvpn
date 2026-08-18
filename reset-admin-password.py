#!/usr/bin/env python3
import os
import secrets
import sqlite3
import string
from pathlib import Path

from werkzeug.security import generate_password_hash


def load_env(path="/etc/vpn-panel.env"):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def make_password(length=14):
    chars = string.ascii_letters + string.digits
    while True:
        value = "".join(secrets.choice(chars) for _ in range(length))
        if any(c.islower() for c in value) and any(c.isupper() for c in value) and any(c.isdigit() for c in value):
            return value


load_env()
db_path = os.environ.get("DATABASE_PATH", "/var/lib/vpn-panel/panel.db")
password = make_password()
conn = sqlite3.connect(db_path)
row = conn.execute("SELECT id,session_version FROM admins ORDER BY id LIMIT 1").fetchone()
if not row:
    raise SystemExit("未找到管理员账户")
next_version = int(row[1] or 1) + 1
conn.execute(
    "UPDATE admins SET username='admin',password_hash=?,session_version=? WHERE id=?",
    (generate_password_hash(password, method="scrypt"), next_version, row[0]),
)
conn.commit()
conn.close()
print("管理员用户名：admin")
print(f"新的随机密码：{password}")
print("所有旧后台会话已失效。")
