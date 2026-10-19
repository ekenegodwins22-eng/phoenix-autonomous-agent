import base64, hashlib, hmac, json, os, sqlite3, time
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field
from app.config import settings
from app.db import connect, using_postgres

bearer = HTTPBearer(auto_error=False)

class AuthPayload(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)

class UserView(BaseModel):
    id: int
    email: str
    created_at: int


def _connect():
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS users (id %s PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL)" % ('BIGSERIAL' if using_postgres() else 'INTEGER'))
    return conn


def _hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return base64.urlsafe_b64encode(salt).decode().rstrip('=') + '.' + base64.urlsafe_b64encode(derived).decode().rstrip('=')


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_s, digest_s = stored.split('.', 1)
        salt = base64.urlsafe_b64decode(salt_s + '=' * (-len(salt_s) % 4))
        expected = base64.urlsafe_b64decode(digest_s + '=' * (-len(digest_s) % 4))
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _token(user_id: int, email: str) -> str:
    now = int(time.time())
    payload = _b64(json.dumps({"sub": user_id, "email": email, "iat": now, "exp": now + settings.auth_token_hours*3600}, separators=(',', ':')).encode())
    signature = _b64(hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + '.' + signature


def _decode_token(token: str):
    try:
        payload, signature = token.split('.', 1)
        expected = _b64(hmac.new(settings.auth_secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected): raise ValueError('bad signature')
        data = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if int(data['exp']) < int(time.time()): raise ValueError('expired')
        return data
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired access token")


def register_user(email: str, password: str):
    normalized = email.strip().lower()
    conn = _connect()
    try:
        cur = conn.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", (normalized, _hash_password(password), int(time.time())))
        conn.commit()
        row = conn.execute("SELECT id,email,created_at FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)
    except Exception as exc:
        if isinstance(exc, sqlite3.IntegrityError) or exc.__class__.__name__ == 'UniqueViolation':
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        raise
    finally:
        conn.close()


def login_user(email: str, password: str):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    finally:
        conn.close()
    if not row or not _verify_password(password, row['password_hash']):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": _token(row['id'], row['email']), "token_type": "bearer", "expires_in": settings.auth_token_hours*3600,
            "user": {"id": row['id'], "email": row['email'], "created_at": row['created_at']}}


async def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Sign in with email and password to continue")
    data = _decode_token(credentials.credentials)
    return {"id": int(data['sub']), "email": data['email']}
