import os
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from flask import Flask, jsonify, request
from sqlalchemy import Column, String, Boolean, ARRAY, create_engine, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, sessionmaker

# ========= Config =========
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://postgres:postgres@db:5432/emrdb"
)
JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME")
JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_AUD = os.getenv("JWT_AUD", "emr-gateway")
JWT_ISS = os.getenv("JWT_ISS", "auth-service")
ACCESS_TTL_MIN = int(os.getenv("ACCESS_TTL_MIN", "30"))
SEED_USER = os.getenv("SEED_USER", "0") == "1"

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


# ========= Model =========
class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    roles = Column(ARRAY(String), nullable=False, server_default=text("'{}'::text[]"))
    scopes = Column(ARRAY(String), nullable=False, server_default=text("'{}'::text[]"))
    is_active = Column(Boolean, nullable=False, server_default=text("true"))


# ========= Helpers =========
def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_pw(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


def make_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "sub": str(user.id),
        "roles": user.roles or [],
        "scopes": user.scopes or [],
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ACCESS_TTL_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def seed_user():
    if not SEED_USER:
        return
    with SessionLocal() as db:
        u = db.query(User).filter_by(username="alice").first()
        if not u:
            u = User(
                username="alice",
                password_hash=hash_pw("secret"),
                roles=["MEDICO"],
                scopes=[
                    "patients:read",
                    "records:read",
                    "records:write",
                    "scheduling:read",
                ],
            )
            db.add(u)
            db.commit()


# ========= App =========
app = Flask(__name__)


@app.get("/health")
def health():
    return {"status": "ok", "service": "auth-service"}, 200


@app.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"error": "missing_credentials"}), 400
    with SessionLocal() as db:
        u = db.query(User).filter_by(username=username).first()
        if not u or not u.is_active or not check_pw(password, u.password_hash):
            return jsonify({"error": "invalid_credentials"}), 401
        token = make_token(u)
        return jsonify({"access_token": token, "token_type": "Bearer"}), 200


@app.post("/auth/refresh")
def refresh():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "unauthorized"}), 401
    token = auth.split()[1]
    try:
        payload = jwt.decode(
            token, JWT_SECRET, algorithms=[JWT_ALG], audience=JWT_AUD, issuer=JWT_ISS
        )
    except Exception:
        return jsonify({"error": "unauthorized"}), 401

    user_id = payload.get("sub")
    with SessionLocal() as db:
        u = db.query(User).get(uuid.UUID(user_id))
        if not u or not u.is_active:
            return jsonify({"error": "unauthorized"}), 401
        new_token = make_token(u)
        return jsonify({"access_token": new_token, "token_type": "Bearer"}), 200


if __name__ == "__main__":
    seed_user()
    app.run(host="0.0.0.0", port=7001)
