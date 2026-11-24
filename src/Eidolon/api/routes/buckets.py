"""
bucket routing
"""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Path, Response

from .. import db
from ..schemas import BucketCreate, BucketName, BucketOut, ErrorOut

router = APIRouter(prefix="/buckets", tags=["buckets"])

BucketPath = Annotated[BucketName, Path(description="Bucket Name")]


@router.get("", response_model=list[BucketOut])
async def list_buckets():
    return await db.list_buckets()


@router.post(
    "",
    response_model=BucketOut,
    status_code=201,
    responses={409: {"model": ErrorOut, "description": "Bucket already exists"}},
)
async def create_bucket(body: BucketCreate):
    return await db.create_bucket(body.name)


@router.delete(
    "/{bucket}",
    status_code=204,
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut, "description": "Bucket not empty (use ?force=true)"}
    },
)
async def delete_bucket(bucket: BucketPath, force: bool = False):
    await db.delete_bucket(bucket, force=force)
    return Response(status_code=204)

