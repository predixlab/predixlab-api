from datetime import datetime, timedelta, timezone
from typing import Optional, Union
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
import secrets
import string
from app.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt

def verify_token(token: str) -> dict:
    """Verify and decode JWT token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        token_type = payload["type"] if "type" in payload else "access"
        if token_type != "access":
            raise JWTError("Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

def generate_verification_code(length: int = 6) -> str:
    """Generate a random verification code"""
    digits = string.digits
    return ''.join(secrets.choice(digits) for _ in range(length))

def generate_session_token() -> str:
    """Generate a secure session token"""
    return secrets.token_urlsafe(32)

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT refresh token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt

def verify_refresh_token(token: str) -> dict:
    """Verify and decode JWT refresh token"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        token_type = payload.get("type")

        if token_type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def generate_refresh_token_string() -> str:
    """Generate a secure refresh token string"""
    return secrets.token_urlsafe(64)

def create_email_verification_token(email: str) -> str:
    """Create token for email verification"""
    data = {"sub": email, "type": "email_verification"}
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    data.update({"exp": expire})
    return jwt.encode(data, settings.secret_key, algorithm=settings.algorithm)

def verify_email_verification_token(token: str) -> Optional[str]:
    """Verify email verification token and return email"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        email: str = payload.get("sub")
        token_type: str = payload.get("type")

        if email is None or token_type != "email_verification":
            return None
        return email
    except JWTError:
        return None

def is_premium_user(
    user_role: str,
    premium_expires_at: Optional[datetime] = None,
    referral_access_expires_at: Optional[datetime] = None,
) -> bool:
    """Check if user has premium access"""
    if user_role == "admin":
        return True

    if (
        referral_access_expires_at is not None
        and datetime.now(timezone.utc)
        < referral_access_expires_at.replace(tzinfo=timezone.utc)
    ):
        return True

    if user_role == "premium":
        if premium_expires_at is None:
            return True  # Lifetime premium

        return datetime.now(timezone.utc) < premium_expires_at.replace(tzinfo=timezone.utc)

    return False


def effective_premium_fields(
    user_role: str,
    premium_expires_at: Optional[datetime] = None,
    referral_access_expires_at: Optional[datetime] = None,
) -> tuple[str, Optional[datetime]]:
    """Return legacy role/expiry fields with referral access folded in."""
    if not is_premium_user(
        user_role,
        premium_expires_at,
        referral_access_expires_at,
    ):
        return user_role, premium_expires_at

    if user_role == "admin" or (
        user_role == "premium" and premium_expires_at is None
    ):
        return user_role, premium_expires_at

    expirations = [
        value
        for value in (premium_expires_at, referral_access_expires_at)
        if value is not None
    ]
    return "premium", max(expirations) if expirations else None

def can_access_recent_data(user_role: str, premium_expires_at: Optional[datetime] = None, data_date: datetime = None) -> bool:
    """Check if user can access recent data (within free user limit)"""
    # Admin and premium users can access all data
    if is_premium_user(user_role, premium_expires_at):
        return True

    # Free users can only access data older than the limit
    if data_date is None:
        return False

    print("Data date:", data_date.tzinfo)

    limit_date = datetime.now(timezone.utc) - timedelta(days=settings.free_user_days_limit)
    return data_date <= limit_date
