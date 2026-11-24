"""
Eidolon API entrypoint

Run uvicorn Eidolon.api.main:app --reload or eidolon-api console script
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import db
from .routes import buckets, objects
from .schemas import Health


# index.html lives at repo root; override for containers/installed packages
INDEX_HTML = Path(
    os.environ.get("INDEX_HTML", Path(__file__).resolve().parents[3] / "index.html")
)

# Same-origin serving needs no CORS Set CORS_ORIGINS="http://localhost:5173,https://x.org"
# only if a separate frontend must call the API. Empty = CORS middleware not installed
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.open_pool()
    try:
        yield
    finally:
        await db.close_pool()


app = FastAPI(
    title="Eidolon",
    description="Image object store on PostgreSQL",
    version="0.1.0",
    lifespan=lifespan
)

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_methods=["GET", "HEAD", "PUT", "POST", "DELETE"],
        allow_headers=["Content-Type", "If-None-Match"],
        expose_headers=["Etag", "Content-Length"],
    )

# --- Domain Errors -> HTTP (keep status codes out of db.py) ---

def _error(status: int, ex: Exception) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": str(ex)})


@app.exception_handler(db.BucketNotFound)
@app.exception_handler(db.ObjectNotFound)
async def _not_found(_: Request, ex: Exception):
    return _error(404, ex)


@app.exception_handler(db.BucketExists)
@app.exception_handler(db.BucketNotEmpty)
async def _conflict(_: Request, ex: Exception):
    return _error(409, ex)


@app.exception_handler(db.InvalidInput)
async def _invalid(_: Request, ex: Exception):
    return _error(422, ex)


@app.exception_handler(psycopg.OperationalError)
async def _db_down(_: Request, ex: Exception):
    # Don't leak connection strings or driver details to clients
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


# --- Routers ---

app.include_router(buckets.router) # /buckets ...
app.include_router(objects.router) # /buckets/{bucket}/objects ...

# --- App-level routes ---

@app.get("/healthz", response_model=Health, tags=["meta"])
async def healthz() -> Health:
    await db.ping() # raises OperationalError -> 503
    return Health()


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_HTML, media_type="text/html")


def run() -> None:
    """Console-script entrypoint: `eidolon-api`"""
    import uvicorn

    uvicorn.run(
        "Eidolon.api.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )

