"""Volatile task-scoped credential generation.

Values are generated with the OS-backed secrets module and are never logged or
returned in task history. The runner owns the per-task dictionary that holds
these values only until the task exits.
"""
import secrets
import string


def generate_password(length: int = 20) -> str:
    length = max(12, min(int(length), 128))
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    value = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*()-_=+"),
    ]
    value.extend(secrets.choice(alphabet) for _ in range(length - len(value)))
    secrets.SystemRandom().shuffle(value)
    return "".join(value)


def generate_username(prefix: str = "phoenix") -> str:
    safe_prefix = "".join(ch for ch in str(prefix).lower() if ch.isalnum())[:20] or "phoenix"
    return f"{safe_prefix}_{secrets.token_hex(5)}"


def generate_value(kind: str, length: int = 20, prefix: str = "phoenix") -> str:
    kind = str(kind or "").lower()
    if kind in {"password", "passphrase"}:
        return generate_password(length)
    if kind in {"username", "user_name", "login"}:
        return generate_username(prefix)
    raise ValueError("Supported generated credential kinds: username, password")
