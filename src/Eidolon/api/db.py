"""
Database access layer for the FastAPI service.
Owns the async connection pool and SQL statements.
Routes do not write SQL but call the functions and catch domain errors defined.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from psycopg import errors as pgerr
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://pgstore:pgstore-dev@localhost:5432/pgstore"
)
CHUNK_SIZE = 1024 * 1024 # 1Mb slices streaming a download
OBJECT_META = """
    o.id, o.bucket_id, o.key, o.content_type, o.size_bytes,
    o.checksum_sha256, o.status, o.created_at, o.updated_at
"""

Row = dict[str, Any]

# --- Domain Errors HTTP route mapping ---

class DBError(Exception): ...
class BucketNotFound(DBError): ... #404
class ObjectNotFound(DBError): ... #404
class BucketExists(DBError): ...   #409
class BucketNotEmpty(DBError): ... #409
class InvalidInput(DBError): ...   #422

# --- Pool lifecycle ---

pool: AsyncConnectionPool | None = None

async def open_pool() -> None:
    global pool
    pool = AsyncConnectionPool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
        kwargs={"row_factory": dict_row},
        open=False
    )
    await pool.open(wait=True)


async def close_pool() -> None:
    if pool is not None:
        await pool.close()


async def ping() -> bool:
    """For GET /healthz"""
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
    return True


# --- Buckets ---

async def list_buckets() -> list[Row]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, created_at FROM buckets ORDER BY name"
        )
        return await cur.fetchall()


async def create_bucket(name: str) -> Row:
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO buckets (name) VALUES (%s) "
                "RETURNING id, name, created_at",
                (name,),
            )
            return await cur.fetchone()
    except pgerr.UniqueViolation as ex:
        raise BucketExists(f"bucket already exists: {name}") from ex


async def delete_bucket(name: str, *, force: bool = False) -> None:
    """Objects cascade on delete, so refuse non-empty buckets unless force=True"""
    async with pool.connection() as conn:
        bucket_id = await _bucket_id(conn, name)
        if not force:
            cur = await conn.execute(
                "SELECT EXISTS (SELECT 1 FROM objects WHERE bucket_id = %s) AS has",
                (bucket_id,),
            )
            if (await cur.fetchone())["has"]:
                raise BucketNotEmpty(f"bucket not empty: {name}")
        await conn.execute("DELETE FROM buckets WHERE id = %s", (bucket_id,))


# --- Objects: metadata ---

async def list_objects(
    bucket: str, *, prefix: str | None = None, limit: int = 100, offset: int = 0
) -> list[Row]:
    async with pool.connection() as conn:
        bucket_id = await _bucket_id(conn, bucket)
        cur = await conn.execute(
            f"""
            SELECT {OBJECT_META}
            FROM objects o
            WHERE o.bucket_id = %(b)s
                AND (%(prefix)s::text IS NULL OR starts_with(o.key, %(prefix)s))
            ORDER BY o.created_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"b": bucket_id, "prefix": prefix, "limit": limit, "offset": offset},
        )
        return await cur.fetchall()


async def get_object_meta(bucket: str, key: str) -> Row:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            SELECT {OBJECT_META}
            FROM objects o JOIN buckets b on b.id = o.bucket_id
            WHERE b.name = %s AND o.key = %s
            """,
            (bucket, key),
        )
        row = await cur.fetchone()
        if row is None:
            raise ObjectNotFound(f"object not found: {bucket}/{key}")
        return row


# --- Objects: write ---

async def put_object(
    bucket: str, key: str, content_type: str, data: bytes, sha256_hex: str | None = None
) -> Row:
    """Upsert via the put_object() SQL function. Returns metadata"""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM put_object(%s, %s, %s, %s, %s)",
                (bucket, key, content_type, data, sha256_hex),
            )
            return await cur.fetchone()
    except pgerr.NoDataFound as ex:  # SQLSTATE P0002 (missing bucket)
        raise BucketNotFound(f"bucket not found: {bucket}") from ex
    except pgerr.InvalidParameterValue as ex:  # SQLSTATE 22023 (empty key / no data)
        raise InvalidInput(str(ex)) from ex


async def delete_object(bucket: str, key: str) -> None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM objects o USING buckets b "
            "WHERE b.id = o.bucket_id AND b.name = %s AND o.key = %s",
            (bucket, key),
        )
        if cur.rowcount == 0:
            raise ObjectNotFound(f"object not found: {bucket}/{key}")


# --- Objects: download ---

async def stream_object_data(
        object_id: UUID, size: int, chunk_size: int = CHUNK_SIZE
) -> AsyncIterator[bytes]:
    """
    Yield payload in slices so a large bytea is not held in application memory
    Caller fetches get_object_meta() first (for headers and 404). Each slice
    borrows a pooled connection only briefly so a slow client does not hold
    a connection open
    """
    offset = 1 # SQL substring() is 1-indexed
    while offset <= size:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT substring(data FROM %s FOR %s) AS chunk FROM objects WHERE id = %s",
                (offset, chunk_size, object_id),
            )
            row = await cur.fetchone()
        if row is None: # deleted mid-stream
            return
        yield bytes(row["chunk"])
        offset += chunk_size


# --- Database Internal Helper Functions ---

async def _bucket_id(conn, name: str) -> UUID:
    cur = await conn.execute(
        "SELECT id FROM buckets WHERE name = %s", (name,)
    )
    row = await cur.fetchone()
    if row is None:
        raise BucketNotFound(f"bucket not found: {name}")
    return row["id"]
