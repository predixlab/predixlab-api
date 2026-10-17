from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from datetime import datetime, timezone, timedelta
import logging

from app.core.security import verify_token
from app.services.user_service import user_service
from app.models.user import UserInDB, UserRole
from app.config import settings
from app.services.referral_service import referral_service

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> UserInDB:
    """Get current authenticated user, with debug mode support"""
    # Production mode - require valid credentials
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Verify token
        payload = verify_token(credentials.credentials)
        user_id: str = payload.get("sub")

        if user_id is None:
            raise credentials_exception

    except Exception:
        raise credentials_exception

    # Get user from database
    user = await user_service.get_user_by_id(user_id)
    if user is None:
        raise credentials_exception

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    if user.referral_reward_pending:
        try:
            user = await referral_service.reconcile_user(user)
        except Exception:
            logger.exception(
                "Failed to reconcile referral reward for user %s",
                user.id,
            )

    return user

async def get_current_active_user(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    """Get current active user"""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user

async def get_optional_current_user(request: Request) -> Optional[UserInDB]:
    """Get current user if authenticated, otherwise return None"""
    authorization: str = request.headers.get("Authorization")

    if not authorization:
        return None

    try:
        scheme, credentials = authorization.split()
        if scheme.lower() != "bearer":
            return None

        # Verify token
        payload = verify_token(credentials)
        user_id: str = payload.get("sub")

        if user_id is None:
            return None

        # Get user from database
        user = await user_service.get_user_by_id(user_id)
        if user is None or not user.is_active:
            return None

        return user

    except Exception as e:
        logger.debug(f"Optional authentication failed: {e}")
        return None

async def require_premium_user(
    current_user: UserInDB = Depends(get_current_user),
) -> UserInDB:
    """Require user to be premium or admin"""
    if not await user_service.is_premium_active(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium membership required"
        )
    return current_user

def require_admin_user(current_user: UserInDB = Depends(get_current_user)) -> UserInDB:
    """Require user to be admin"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user

async def get_user_agent(request: Request) -> str:
    """Get user agent from request"""
    return request.headers.get("user-agent", "Unknown")

async def get_client_ip(request: Request) -> str:
    """Get client IP address from request"""
    # Check for forwarded headers first
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP if there are multiple
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback to client host
    if hasattr(request.client, "host"):
        return request.client.host

    return "Unknown"
