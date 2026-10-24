"""JWT-authenticated management of least-privilege MCP credentials."""

from typing import Annotated
from urllib.parse import urlsplit
from ipaddress import ip_address

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response

from app.config import settings
from app.core.dependencies import get_current_user
from app.core.rate_limits import enforce_agent_key_issue_limit
from app.models.agent import (
    AgentApiKeyCreate,
    AgentApiKeyIssued,
    AgentApiKeyList,
    AgentApiKeyVersion,
    AgentConnection,
)
from app.models.user import UserInDB
from app.services.agent_key_service import agent_key_service


def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


router = APIRouter(
    prefix="/agent",
    tags=["Agent access"],
    dependencies=[Depends(private_response)],
)
KeyId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@router.get(
    "/connection",
    response_model=AgentConnection,
    dependencies=[Depends(get_current_user)],
)
async def get_agent_connection(request: Request) -> AgentConnection:
    try:
        url = urlsplit(settings.public_api_url)
        if url.port == 0:
            raise ValueError("Port zero is not a public origin")
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="Agent access requires a valid PUBLIC_API_URL.",
        ) from exc
    if (
        not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or not (
            url.scheme == "https"
            or (
                url.scheme == "http"
                and url.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Agent access requires a valid HTTPS PUBLIC_API_URL (or local loopback HTTP).",
        )
    if _is_loopback_host(url.hostname) and not _is_loopback_host(request.url.hostname):
        raise HTTPException(
            status_code=503,
            detail="PUBLIC_API_URL must be the public HTTPS API origin for remote agent connections.",
        )
    return AgentConnection(mcp_url=f"{url.scheme}://{url.netloc}/mcp/")


@router.get("/api-keys", response_model=AgentApiKeyList)
async def list_agent_api_keys(
    current_user: UserInDB = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> AgentApiKeyList:
    return await agent_key_service.list(current_user.id, limit, offset)


@router.post("/api-keys", response_model=AgentApiKeyIssued, status_code=201)
async def create_agent_api_key(
    request: AgentApiKeyCreate,
    current_user: UserInDB = Depends(get_current_user),
) -> AgentApiKeyIssued:
    await enforce_agent_key_issue_limit(current_user.id)
    return await agent_key_service.create(current_user.id, request)


@router.post("/api-keys/{key_id}/rotate", response_model=AgentApiKeyIssued)
async def rotate_agent_api_key(
    key_id: KeyId,
    request: AgentApiKeyVersion,
    current_user: UserInDB = Depends(get_current_user),
) -> AgentApiKeyIssued:
    await enforce_agent_key_issue_limit(current_user.id)
    return await agent_key_service.rotate(current_user.id, key_id, request.version)


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_agent_api_key(
    key_id: KeyId,
    version: int = Query(..., ge=1),
    current_user: UserInDB = Depends(get_current_user),
) -> None:
    await agent_key_service.revoke(current_user.id, key_id, version)
