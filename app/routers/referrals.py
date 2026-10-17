from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.models.referral import (
    ReferralClickRequest,
    ReferralSummary,
    ReferralValidationResponse,
)
from app.models.user import UserInDB
from app.services.referral_service import referral_service

router = APIRouter(prefix="/referrals", tags=["Referrals"])


@router.post(
    "/click",
    response_model=ReferralValidationResponse,
    summary="Record a referral landing",
)
async def track_referral_click(
    request: ReferralClickRequest,
) -> ReferralValidationResponse:
    return await referral_service.track_click(
        request.referral_code,
        request.visitor_id,
    )


@router.get(
    "/summary",
    response_model=ReferralSummary,
    summary="Get the current user's referral performance",
)
async def get_referral_summary(
    current_user: UserInDB = Depends(get_current_user),
) -> ReferralSummary:
    return await referral_service.get_summary(current_user)
