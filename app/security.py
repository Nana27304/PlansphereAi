import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from .config import settings
from .database import get_db
from .models import User, Subscription

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def hash_code(code: str) -> str:
    return hashlib.sha256((settings.secret_key + code).encode()).hexdigest()


def verify_code_hash(code: str, stored: str) -> bool:
    return hmac.compare_digest(hash_code(code), stored)


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user.id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    try:
        payload = jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired. Please sign in again.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found")

    # Paid access is time-bounded. Manual MoMo/bank-transfer renewals expire
    # unless a verified new payment extends the subscription period.
    if user.tier == "advanced":
        active = (
            db.query(Subscription)
            .filter_by(user_id=user.id, status="active")
            .order_by(Subscription.created_at.desc())
            .first()
        )
        if active and active.current_period_end and (not active.auto_renew or active.cancel_at_period_end):
            expiry = active.current_period_end
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                active.status = "expired"
                user.tier = "professional"
                db.commit()

    return user


def require_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.email_verified or not user.phone_verified:
        raise HTTPException(status_code=403, detail="Verify both your email address and phone number first")
    return user
