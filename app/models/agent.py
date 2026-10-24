"""Public contracts for MCP-only API credentials."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

KeyLifetime = Literal[30, 90, 365]


class AgentConnection(BaseModel):
    mcp_url: str
    scope: Literal["mcp:read"] = "mcp:read"
    default_expires_in_days: Literal[90] = 90
    expires_in_days_options: list[KeyLifetime] = Field(default_factory=lambda: [30, 90, 365])


class AgentApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    expires_in_days: KeyLifetime = 90


class AgentApiKeyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, strict=True)


class AgentApiKey(BaseModel):
    id: str
    name: str
    prefix: str
    scope: Literal["mcp:read"] = "mcp:read"
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    status: Literal["active", "expired", "revoked"]
    version: int
    expires_in_days: KeyLifetime


class AgentApiKeyIssued(BaseModel):
    api_key: str = Field(repr=False)
    key: AgentApiKey


class AgentApiKeyList(BaseModel):
    items: list[AgentApiKey]
    total_count: int
    limit: int
    offset: int
    max_keys: int
    non_revoked_count: int
