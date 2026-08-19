from pathlib import Path

import requests
from flask import current_app

from .crypto import decrypt_text
from .settings_store import get_settings


def _credentials():
    values = get_settings()
    token_enc = (values.get("telegram_bot_token_enc") or "").strip()
    chat_id = (values.get("telegram_chat_id") or "").strip()
    if not token_enc or not chat_id:
        raise ValueError("Telegram Bot Token 或 Chat ID 尚未配置")
    try:
        token = decrypt_text(current_app, token_enc)
    except Exception as exc:
        raise ValueError("Telegram Bot Token 无法解密，请重新保存") from exc
    if not token:
        raise ValueError("Telegram Bot Token 为空")
    return token, chat_id


def _api_url(token: str, method: str):
    return f"https://api.telegram.org/bot{token}/{method}"


def test_connection(token=None, chat_id=None):
    if not token or not chat_id:
        saved_token, saved_chat_id = _credentials()
        token = token or saved_token
        chat_id = chat_id or saved_chat_id
    try:
        me = requests.get(_api_url(token, "getMe"), timeout=12)
        me.raise_for_status()
        data = me.json()
        if not data.get("ok"):
            raise ValueError(data.get("description") or "Bot Token 验证失败")
        bot_name = data.get("result", {}).get("username") or data.get("result", {}).get("first_name") or "Telegram Bot"
        resp = requests.post(
            _api_url(token, "sendMessage"),
            data={"chat_id": chat_id, "text": "XVPN Panel\n\nTelegram 自动备份通知配置成功。"},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise ValueError(payload.get("description") or "测试消息发送失败")
        return bot_name
    except requests.RequestException as exc:
        raise ValueError(f"Telegram 网络请求失败：{exc}") from exc


def send_message(text: str):
    token, chat_id = _credentials()
    try:
        resp = requests.post(
            _api_url(token, "sendMessage"),
            data={"chat_id": chat_id, "text": text},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise ValueError(payload.get("description") or "Telegram 消息发送失败")
        return True
    except requests.RequestException as exc:
        raise ValueError(f"Telegram 网络请求失败：{exc}") from exc


def send_backup(path, caption: str):
    token, chat_id = _credentials()
    path = Path(path)
    if not path.is_file():
        raise ValueError("要发送的备份文件不存在")
    # Telegram cloud Bot API currently accepts up to 50 MB for multipart documents.
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("备份超过 Telegram Bot API 50MB 文件上传限制，请从网页下载保存")
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                _api_url(token, "sendDocument"),
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"document": (path.name, fh, "application/zip")},
                timeout=90,
            )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise ValueError(payload.get("description") or "Telegram 备份发送失败")
        return True
    except requests.RequestException as exc:
        raise ValueError(f"Telegram 网络请求失败：{exc}") from exc
