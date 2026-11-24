"""
object routing
"""

from __future__ import annotations

import hashlib
import os
from datetime import timezone
from email.utils import format_datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse


from .. import db
from ..schemas import (
    BucketName, ErrorOut, ObjectKey, ObjectList, ObjectListParams, ObjectMeta,
)

router = APIRouter(prefix="/buckets/{bucket}/objects", tags=["objects"])

BucketPath = Annotated[BucketName, Path(description="Bucket Name")]
KeyPath = Annotated[ObjectKey, Path(description="Object key, may contain '/'")]

# The whole body buffered before INSERT (bytea cannot be streamed), so needs to be capped
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
DEFAULT_CT = "application/octet-stream"

def _download_headers(m: dict) -> dict[str, str]:
    """
    Headers shared by GET and HEAD
    Uploaded content is served from the same origin as the UI
    A stored text/html or SVG would run as script
    Only raster images are shown inline
    Everything else is forced to download, and a sandbox CSP + nosniff cover the rest
    """

    ct = m["content_type"]
    inline = ct.startswith("image/") and ct != "image/svg+xml"
    filename = quote(m["key"].rsplit("/", 1)[-1], safe="")
    headers = {
        "Content-Length": str(m["size_bytes"]),
        "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        # HTTP-date must be GMT; the DB session timezone is Europe/London, so convert first
        "Last-Modified": format_datetime(m["updated_at"].astimezone(timezone.utc), usegmt=True),
    }
    if m["checksum_sha256"]:
        headers["ETag"] = f'"{m["checksum_sha256"]}"'
    return headers


# --- List ---

@router.get("", response_model=ObjectList, responses={404: {"model": ErrorOut}})
async def list_objects(bucket: BucketPath, params: Annotated[ObjectListParams, Query()]):
    # ask for one extra row to learn whether another exists
    rows = await db.list_objects(
        bucket, prefix=params.prefix, limit=params.limit + 1, offset=params.offset
    )
    return ObjectList(
        items=rows[: params.limit],
        limit=params.limit,
        offset=params.offset,
        has_more=len(rows) > params.limit
    )


# --- Upload (raw bytes) ---

@router.put(
    "/{key:path}",
    response_model=ObjectMeta,
    responses={404: {"model": ErrorOut}, 413: {"model": ErrorOut}, 422: {"model": ErrorOut}},
)
async def put_object(bucket: BucketPath, key: KeyPath, request: Request):
    """
    Body = the file raw bytes, type comes from the Content-Type header
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"object exceeds {MAX_UPLOAD_BYTES} bytes")

    digest = hashlib.sha256()
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"object exceeds {MAX_UPLOAD_BYTES} bytes")
        digest.update(chunk)

    content_type = request.headers.get("content-type") or DEFAULT_CT
    # Roadmap: verify magic bytes against content_type here (415 on mismatch)

    return await db.put_object(bucket, key, content_type, bytes(buf), digest.hexdigest())


# --- Download / metadata ---

@router.head("/{key:path}", responses={404: {"model": ErrorOut}})
async def head_object(bucket: BucketPath, key: KeyPath):
    m = await db.get_object_meta(bucket, key)
    return Response(media_type=m["content_type"], headers=_download_headers(m))


@router.get(
    "/{key:path}",
    response_model=None, # bytes by default ObjectMeta JSON when ?meta=true
    responses={200: {"content": {"application/octet-stream": {}}},
               304: {"description": "Not modified"}, 404: {"model": ErrorOut}},
)
async def get_object(
        bucket: BucketPath, key: KeyPath, request: Request, meta: bool = False
):
    m = await db.get_object_meta(bucket, key) # 404 happens here, before streaming
    if meta:
        return ObjectMeta.model_validate(m)

    headers = _download_headers(m)
    if "ETag" in headers and request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers={"ETag": headers["ETag"]})

    return StreamingResponse(
        db.stream_object_data(m["id"], m["size_bytes"]),
        media_type=m["content_type"],
        headers=headers,
    )


# --- Delete ---

@router.delete("/{key:path}", status_code=204, responses={404: {"model": ErrorOut}})
async def delete_object(bucket: BucketPath, key: KeyPath):
    await db.delete_object(bucket, key)
    return Response(status_code=204)

