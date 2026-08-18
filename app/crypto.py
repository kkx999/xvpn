from cryptography.fernet import Fernet


def ensure_crypto_ready(app):
    key = app.config.get("FERNET_KEY", "")
    if not key:
        raise RuntimeError("FERNET_KEY is required. Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
    try:
        Fernet(key.encode())
    except Exception as exc:
        raise RuntimeError("FERNET_KEY is invalid") from exc


def encrypt_text(app, value: str) -> str:
    return Fernet(app.config["FERNET_KEY"].encode()).encrypt(value.encode()).decode()


def decrypt_text(app, value: str) -> str:
    return Fernet(app.config["FERNET_KEY"].encode()).decrypt(value.encode()).decode()
