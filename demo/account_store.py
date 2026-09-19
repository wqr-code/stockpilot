"""Small account/session store for the hosted single-process edition."""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import os
import secrets
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError


def _hash_password(password, iterations=310_000):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f'pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}'


def _verify_password(password, encoded):
    try:
        _, rounds, salt, expected = encoded.split('$', 3)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(rounds))
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class AccountStore:
    def __init__(self, url=None):
        url = url or os.getenv('DATABASE_URL') or 'sqlite:///demo/data/stockpilot.local.db'
        if url.startswith('postgres://'):
            url = 'postgresql+psycopg://' + url[len('postgres://'):]
        elif url.startswith('postgresql://'):
            url = 'postgresql+psycopg://' + url[len('postgresql://'):]
        self.engine = create_engine(url, pool_pre_ping=True)
        self._prepare()

    def _prepare(self):
        statements = (
            'CREATE TABLE IF NOT EXISTS users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(254) UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at VARCHAR(40) NOT NULL)',
            'CREATE TABLE IF NOT EXISTS sessions (token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(36) NOT NULL, expires_at VARCHAR(40) NOT NULL)',
            'CREATE TABLE IF NOT EXISTS workspaces (user_id VARCHAR(36) PRIMARY KEY, payload TEXT NOT NULL, updated_at VARCHAR(40) NOT NULL)',
        )
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    def register(self, email, password):
        user_id = str(uuid.uuid4())
        normalized = email.strip().lower()
        try:
            with self.engine.begin() as conn:
                conn.execute(text('INSERT INTO users (id,email,password_hash,created_at) VALUES (:id,:email,:password,:created)'),
                    {'id': user_id, 'email': normalized, 'password': _hash_password(password), 'created': datetime.now(timezone.utc).isoformat()})
        except IntegrityError as exc:
            raise ValueError('该邮箱已注册') from exc
        return {'id': user_id, 'email': normalized}

    def authenticate(self, email, password):
        with self.engine.connect() as conn:
            row = conn.execute(text('SELECT id,email,password_hash FROM users WHERE email=:email'),
                {'email': email.strip().lower()}).mappings().first()
        if not row or not _verify_password(password, row['password_hash']):
            return None
        return {'id': row['id'], 'email': row['email']}

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=30)
        with self.engine.begin() as conn:
            conn.execute(text('INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (:token,:user,:expires)'),
                {'token': hashlib.sha256(token.encode()).hexdigest(), 'user': user_id, 'expires': expires.isoformat()})
        return token

    def session_user(self, token):
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        with self.engine.begin() as conn:
            row = conn.execute(text('SELECT u.id,u.email,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=:token'),
                {'token': token_hash}).mappings().first()
            if row and row['expires_at'] <= now:
                conn.execute(text('DELETE FROM sessions WHERE token_hash=:token'), {'token': token_hash})
                return None
        return {'id': row['id'], 'email': row['email']} if row else None

    def delete_session(self, token):
        if not token:
            return
        with self.engine.begin() as conn:
            conn.execute(text('DELETE FROM sessions WHERE token_hash=:token'),
                {'token': hashlib.sha256(token.encode()).hexdigest()})

    def save_workspace(self, user_id, payload):
        with self.engine.begin() as conn:
            conn.execute(text('DELETE FROM workspaces WHERE user_id=:user'), {'user': user_id})
            conn.execute(text('INSERT INTO workspaces (user_id,payload,updated_at) VALUES (:user,:payload,:updated)'),
                {'user': user_id, 'payload': payload, 'updated': datetime.now(timezone.utc).isoformat()})

    def load_workspace(self, user_id):
        with self.engine.connect() as conn:
            return conn.execute(text('SELECT payload FROM workspaces WHERE user_id=:user'),
                {'user': user_id}).scalar_one_or_none()
