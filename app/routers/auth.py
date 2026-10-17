from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
import httpx
from typing import Dict, Any, Optional
import logging

from app.models.user import (
    EmailLoginRequest, EmailRegisterCodeRequest, EmailLoginVerify, UserRegister, Token,
    UserRole, OAuthProvider, RefreshTokenRequest, OAuthUserInfo, OAuthConfirmRequest,
    OAuthLinkRequest, OAuthExchangeRequest, to_public_user
)
from app.services.user_service import user_service
from app.core.security import create_access_token, verify_refresh_token
from app.core.dependencies import get_current_user, get_user_agent, get_client_ip
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# OAuth setup
oauth = OAuth()

oauth.register(
    name='google',
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

oauth.register(
    name='microsoft',
    client_id=settings.microsoft_client_id,
    client_secret=settings.microsoft_client_secret,
    access_token_url='https://login.microsoftonline.com/consumers/oauth2/v2.0/token',
    authorize_url='https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize',
    api_base_url='https://graph.microsoft.com/v1.0/',
    client_kwargs={'scope': 'openid email profile User.Read'},
    server_metadata_url='https://login.microsoftonline.com/consumers/v2.0/.well-known/openid-configuration'
)

# ============ Registration Routes ============

@router.post("/register/request-code", summary="Request verification code for registration")
async def request_registration_code(
    request_data: EmailRegisterCodeRequest,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Request email verification code for registration"""
    try:
        # Check if user already exists
        existing_user = await user_service.get_user_by_email(request_data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists"
            )

        # Send verification code for registration
        success = await user_service.send_verification_code(request_data.email, for_registration=True, language=request_data.preferred_language)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send verification code"
            )

        logger.info(f"Registration verification code sent to {request_data.email} from {client_ip}")

        return {
            "message": "Verification code sent to email",
            "email": request_data.email
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send registration verification code to {request_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification code"
        )

@router.post("/register", response_model=Token, summary="Register new user")
async def register_user(
    register_data: UserRegister,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Register a new user with email verification"""
    try:
        # Register user (verification happens inside this method)
        user = await user_service.register_user(register_data)

        # Update last login
        await user_service.update_last_login(user.id)

        # Create access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.id, "email": user.email},
            expires_delta=access_token_expires
        )

        # Create refresh token
        refresh_token = await user_service.create_refresh_token(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=client_ip
        )

        user_response = to_public_user(user)

        logger.info(f"User {register_data.email} registered successfully from {client_ip}")

        return Token(
            access_token=access_token,
            refresh_token=refresh_token.token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=user_response
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to register user {register_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed"
        )

# ============ Login Routes ============

@router.post("/login/request-code", summary="Request email verification code for login")
async def request_login_code(
    request_data: EmailLoginRequest,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Request email verification code for login"""
    try:
        # Check if user exists
        user = await user_service.get_user_by_email(request_data.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User with this email does not exist"
            )

        # Send verification code for login
        success = await user_service.send_verification_code(request_data.email, for_registration=False, language=user.preferred_language)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send verification code"
            )

        logger.info(f"Login verification code sent to {request_data.email} from {client_ip}")

        return {
            "message": "Verification code sent to email",
            "email": request_data.email
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to send login verification code to {request_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send verification code"
        )

@router.post("/login", response_model=Token, summary="Login with email verification code")
async def login_user(
    verify_data: EmailLoginVerify,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Login user with email verification code"""
    try:
        # Verify the code
        is_valid = await user_service.verify_email_code(verify_data.email, verify_data.code)

        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code"
            )

        # Get user (must exist for login)
        user = await user_service.get_user_by_email(verify_data.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User account is inactive"
            )

        # Update last login
        await user_service.update_last_login(user.id)

        # Create access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.id, "email": user.email},
            expires_delta=access_token_expires
        )

        # Create refresh token
        refresh_token = await user_service.create_refresh_token(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=client_ip
        )

        user_response = to_public_user(user)

        logger.info(f"User {verify_data.email} logged in successfully from {client_ip}")

        return Token(
            access_token=access_token,
            refresh_token=refresh_token.token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=user_response
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to login user {verify_data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )

# ============ OAuth Routes ============

@router.get("/google", summary="Initiate Google OAuth login")
async def google_login(
    request: Request,
    referral_code: Optional[str] = Query(default=None, min_length=4, max_length=32),
):
    """Initiate Google OAuth login"""
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured"
        )

    if referral_code:
        request.session["referral_code"] = referral_code
    else:
        request.session.pop("referral_code", None)
    redirect_uri = settings.google_oauth_callback_url
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/google/callback", summary="Google OAuth callback")
async def google_callback(
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Handle Google OAuth callback: issue short-lived code and redirect to frontend"""
    try:
        referral_code = request.session.pop("referral_code", None)
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get('userinfo')

        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user information from Google"
            )

        email = user_info.get('email')
        name = user_info.get('name')
        google_id = user_info.get('sub')

        if not email or not google_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incomplete user information from Google"
            )

        # Check if user exists
        existing_user = await user_service.get_user_by_email(email)

        if existing_user:
            # Existing user: create a short-lived code and redirect
            if not existing_user.is_active:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account is inactive")

            if not existing_user.oauth_provider_id:
                await user_service.update_user(existing_user.id, {
                    "oauth_provider": OAuthProvider.GOOGLE,
                    "oauth_provider_id": google_id,
                    "email_verified": True
                })

            await user_service.update_last_login(existing_user.id)

            code = await user_service.create_login_code(existing_user.id, OAuthProvider.GOOGLE)
            redirect_url = f"{settings.oauth_redirect_url}?provider=google&status=success&code={code}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
        else:
            # New user, create OAuth token for confirmation
            oauth_user_info = OAuthUserInfo(
                email=email,
                name=name,
                provider=OAuthProvider.GOOGLE,
                provider_id=google_id
            )

            oauth_token = await user_service.create_oauth_token(
                oauth_user_info,
                referral_code,
            )

            logger.info(f"OAuth token created for new Google user {email} from {client_ip}")

            # Redirect to frontend for confirmation with oauth_token
            redirect_url = f"{settings.oauth_redirect_url}?provider=google&status=confirm&email={email}&name={name}&oauth_token={oauth_token.token}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google OAuth callback failed: {e}")
    redirect_url = f"{settings.oauth_redirect_url}?provider=google&status=error"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

@router.get("/microsoft", summary="Initiate Microsoft OAuth login")
async def microsoft_login(
    request: Request,
    referral_code: Optional[str] = Query(default=None, min_length=4, max_length=32),
):
    """Initiate Microsoft OAuth login"""
    if not settings.microsoft_client_id or not settings.microsoft_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Microsoft OAuth not configured"
        )

    if referral_code:
        request.session["referral_code"] = referral_code
    else:
        request.session.pop("referral_code", None)
    redirect_uri = settings.microsoft_oauth_callback_url
    return await oauth.microsoft.authorize_redirect(request, redirect_uri)

@router.get("/microsoft/callback", summary="Microsoft OAuth callback")
async def microsoft_callback(
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Handle Microsoft OAuth callback: issue short-lived code and redirect to frontend"""
    try:
        referral_code = request.session.pop("referral_code", None)
        token = await oauth.microsoft.authorize_access_token(request)

        # Get user info from Microsoft Graph API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                'https://graph.microsoft.com/v1.0/me',
                headers={'Authorization': f"Bearer {token['access_token']}"}
            )
            user_info = response.json()

        email = user_info.get('mail') or user_info.get('userPrincipalName')
        name = user_info.get('displayName')
        microsoft_id = user_info.get('id')

        if not email or not microsoft_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Incomplete user information from Microsoft"
            )

        # Check if user exists
        existing_user = await user_service.get_user_by_email(email)

        if existing_user:
            if not existing_user.is_active:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User account is inactive")

            if not existing_user.oauth_provider_id:
                await user_service.update_user(existing_user.id, {
                    "oauth_provider": OAuthProvider.MICROSOFT,
                    "oauth_provider_id": microsoft_id,
                    "email_verified": True
                })

            await user_service.update_last_login(existing_user.id)

            code = await user_service.create_login_code(existing_user.id, OAuthProvider.MICROSOFT)
            redirect_url = f"{settings.oauth_redirect_url}?provider=microsoft&status=success&code={code}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)
        else:
            # New user, create OAuth token for confirmation
            oauth_user_info = OAuthUserInfo(
                email=email,
                name=name,
                provider=OAuthProvider.MICROSOFT,
                provider_id=microsoft_id
            )

            oauth_token = await user_service.create_oauth_token(
                oauth_user_info,
                referral_code,
            )

            logger.info(f"OAuth token created for new Microsoft user {email} from {client_ip}")

            redirect_url = f"{settings.oauth_redirect_url}?provider=microsoft&status=confirm&email={email}&name={name}&oauth_token={oauth_token.token}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Microsoft OAuth callback failed: {e}")
        redirect_url = f"{settings.oauth_redirect_url}?provider=microsoft&status=error"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)

@router.post("/oauth/exchange", response_model=Token, summary="Exchange login code for tokens")
async def exchange_oauth_code(
    payload: OAuthExchangeRequest,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Exchange a short-lived OAuth login code for access/refresh tokens."""
    try:
        consumed = await user_service.consume_login_code(payload.code)
        if not consumed:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired code")

        user = await user_service.get_user_by_id(consumed["user_id"])
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.id, "email": user.email},
            expires_delta=access_token_expires
        )

        refresh_token = await user_service.create_refresh_token(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=client_ip
        )

        return Token(
            access_token=access_token,
            refresh_token=refresh_token.token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=to_public_user(user)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to exchange OAuth code: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Exchange failed")

@router.post("/oauth/confirm", response_model=Token, summary="Confirm OAuth user registration")
async def confirm_oauth_user(
    confirm_data: OAuthConfirmRequest,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Confirm OAuth user registration after frontend confirmation"""
    try:
        if not confirm_data.confirmed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User registration not confirmed"
            )

        # Confirm OAuth user creation
        user = await user_service.confirm_oauth_user(
            confirm_data.oauth_token,
            confirm_data.user_notice_version,
            confirm_data.name,
        )

        # Update last login
        await user_service.update_last_login(user.id)

        # Create access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.id, "email": user.email},
            expires_delta=access_token_expires
        )

        # Create refresh token
        refresh_token = await user_service.create_refresh_token(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=client_ip
        )

        logger.info(f"OAuth user {user.email} confirmed and registered successfully from {client_ip}")

        return Token(
            access_token=access_token,
            refresh_token=refresh_token.token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=to_public_user(user)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to confirm OAuth user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth confirmation failed"
        )

@router.post("/oauth/link", summary="Link OAuth account to existing user")
async def link_oauth_account(
    link_data: OAuthLinkRequest,
    current_user = Depends(get_current_user),
    client_ip: str = Depends(get_client_ip)
):
    """Link OAuth account to currently authenticated user"""
    try:
        # Link OAuth account
        updated_user = await user_service.link_oauth_account(current_user.id, link_data.oauth_token)

        logger.info(f"OAuth account linked to user {current_user.email} from {client_ip}")

        return {
            "message": "OAuth account linked successfully",
            "user": to_public_user(updated_user)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to link OAuth account for user {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth account linking failed"
        )

# ============ Token Management Routes ============

@router.post("/refresh", response_model=Token, summary="Refresh access token")
async def refresh_access_token(
    refresh_request: RefreshTokenRequest,
    request: Request,
    user_agent: str = Depends(get_user_agent),
    client_ip: str = Depends(get_client_ip)
):
    """Refresh access token using refresh token"""
    try:
        # Get refresh token from database
        refresh_token = await user_service.get_refresh_token(refresh_request.refresh_token)

        if not refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token"
            )

        # Get user
        user = await user_service.get_user_by_id(refresh_token.user_id)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive"
            )

        # Create new access token
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        access_token = create_access_token(
            data={"sub": user.id, "email": user.email},
            expires_delta=access_token_expires
        )

        # Create new refresh token and revoke old one
        await user_service.revoke_refresh_token(refresh_request.refresh_token)
        new_refresh_token = await user_service.create_refresh_token(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=client_ip
        )

        user_response = to_public_user(user)

        logger.info(f"Access token refreshed for user {user.email} from {client_ip}")

        return Token(
            access_token=access_token,
            refresh_token=new_refresh_token.token,
            token_type="bearer",
            expires_in=settings.access_token_expire_minutes * 60,
            user=user_response
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to refresh token: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )

@router.post("/logout", summary="Logout user")
async def logout(
    refresh_request: RefreshTokenRequest = None,
    current_user = Depends(get_current_user),
    request: Request = None,
    client_ip: str = Depends(get_client_ip)
):
    """Logout current user and revoke refresh token"""
    try:
        # Revoke refresh token if provided
        if refresh_request and refresh_request.refresh_token:
            await user_service.revoke_refresh_token(refresh_request.refresh_token)

        logger.info(f"User {current_user.email} logged out from {client_ip}")

        return {"message": "Successfully logged out"}

    except Exception as e:
        logger.error(f"Logout error for user {current_user.email}: {e}")
        return {"message": "Successfully logged out"}

@router.post("/logout/all", summary="Logout from all devices")
async def logout_all_devices(
    current_user = Depends(get_current_user),
    client_ip: str = Depends(get_client_ip)
):
    """Logout user from all devices by revoking all refresh tokens"""
    try:
        # Revoke all refresh tokens for the user
        revoked_count = await user_service.revoke_all_user_refresh_tokens(current_user.id)

        logger.info(f"User {current_user.email} logged out from all devices ({revoked_count} tokens revoked) from {client_ip}")

        return {
            "message": "Successfully logged out from all devices",
            "revoked_tokens": revoked_count
        }

    except Exception as e:
        logger.error(f"Logout all devices error for user {current_user.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to logout from all devices"
        )
