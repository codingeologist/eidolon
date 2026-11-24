"""
Pydantic models (request bodies, responses, shared types)
Rows from db.py are plain dicts (dict_row) so are directly
validated by response models: BucketOut.model_validate(row)
Payload bytes travel as raw response/request bodies
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# --- Shared constraint types ---

# Mirror S3 schema nomenclature
BucketName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]

# Object keys free-text like "uploads/example/page1.jpg"
# DB rejects blank keys (22023) Null values rejected and cap length
ObjectKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=1024,
        pattern=r"^[^\x00]+$",
    ),
]

class ObjectStatus(str, Enum):
    """Mirrors Postgres enum object_status"""
    pending = "pending"
    stored = "stored"
    failed = "failed"

# --- Buckets ---

class BucketCreate(BaseModel):
    """body of POST /buckets"""
    model_config = ConfigDict(extra="forbid")

    name: BucketName


class BucketOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime


# --- Objects ---

class ObjectMeta(BaseModel):
    """
    Metadata for one object, the PUT response, HEAD-as-JSON and list items
    Matches the OBJECT_META column list in db.py. Never includes `data`
    """
    id: UUID
    bucket_id: UUID
    key: str
    content_type: str
    size_bytes: int
    checksum_sha256: str | None = None
    status: ObjectStatus
    created_at: datetime
    updated_at: datetime


class ObjectList(BaseModel):
    """Response of GET /buckets/{bucket}/objects"""
    items: list[ObjectMeta]
    limit: int
    offset: int
    # True when a further page exists (route fetches limit+1 rows to decide this)
    has_more: bool


class ObjectListParams(BaseModel):
    """Query parameters for listing; use as `Annotated[ObjectListParams, Query()]`"""
    model_config = ConfigDict(extra="forbid")

    prefix: str | None = Field(default=None, max_length=1024)
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


# --- Misc ---

class Health(BaseModel):
    status: str = "ok"


class ErrorOut(BaseModel):
    """
    Shape of error bodies for OpenAPI `responses=` declarations
    FastAPI HTTPException already emits {"detail":...}
    This is just for documentation
    """
    detail: str

