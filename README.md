# Postgres Image Store

A minimal image object store built on a single PostgreSQL instance serving the buckets, object metadata, and the image bytes. No S3-compatible service required, in the spirit of [Postgres For Everything](https://postgresforeverything.com)

---

```txt
┌────────────┐  REST (JSON + base64)  ┌───────────┐
│ index.html │ ─────────────────────▶ │ PostgREST │   ──┐
└────────────┘                        └───────────┘     │
┌────────────┐   SQL (psycopg)                          ▼
│   app.py   │ ──────────────────────────────▶ ┌────────────┐
└────────────┘   (goes straight to Postgres)   │  Postgres  │
                                               │  buckets   │
                                               │  objects   │
                                               └────────────┘
```

---

## Why?

Postgres can act as a catalogue (object metadata) in relational tables and an object store (payloads as `bytea`, compressed and stored out-of-line by [TOAST](https://www.postgresql.org/docs/current/storage-toast.html)) at the same time. This repo is a working test bed for that idea: a schema and ways to interact with the data, and a place to note design strains.

---

## Storage Model

- `buckets`: named containers using the AWS S3 style.
- `objects`: one row per object, holding `key`, `content_type`, `size_bytes`, `checksum_sha256`, `status`, as well as the `bytea` payload. (`bucket_id` and `key` are unique).
- Payloads are stored as `bytea`. TOAST handles transparent compression and out-of-line storage. `size_bytes` maintained by trigger.
- `store_object()` performs the base64 to binary decode, computes the SHA256 checksum, and upserts on key conflict.
- Full schema in `initdb/01-schema.sql`.

---

## Quickstart

```bash
docker compose up -d
```

The schema and the `store_object` function are created automatically on first start (empty volume only using `docker compose down -v` for a clean slate).

Add a bucket and upload an image:

```bash
# create a bucket
curl -X POST http://localhost:3000/buckets \
  -H "Content-Type: application/json" -d '{"name":"my-images"}'

# upload (base64 payload via RPC; jq --rawfile avoids shell argument limits)
base64 -w0 cat.jpg > /tmp/payload.b64
jq -n --rawfile b64 /tmp/payload.b64 \
   '{p_bucket:"my-images", p_key:"pets/cat.jpg", p_ct:"image/jpeg", p_b64:$b64}' \
   > /tmp/payload.json
curl -X POST http://localhost:3000/rpc/store_object \
     -H "Content-Type: application/json" -d @/tmp/payload.json
```

See [Usage](#Usage) for listing, head-style metadata and deletion

---

## Usage

Three interchangeable methods to interact with the object store:

| Interface            | Method                         | Usage                                     |
|----------------------|--------------------------------|-------------------------------------------|
| Rest API (PostgREST) | `http://localhost:3000`        | `curl`, scripting, web UI                 |
| Web browser UI       | open `index.html` in a browser | image catalogue browsing, upload, preview |
| Terminal UI (TUI)    | `python app.py`                | fast local inspection and image preview   |

---

## REST Endpoints

```bash
# list buckets
curl http://localhost:3000/buckets?select=id,name

# "head object": metadata only, never the payload
curl "http://localhost:3000/objects?key=eq.pets/cat.jpg&select=key,size_bytes,content_type,checksum_sha256,status,created_at"

# delete
curl -X DELETE "http://localhost:3000/objects?key=eq.pets/cat.jpg"
```

**Always restrict `select=` on `objects/`. Otherwise every response ships the binary payload of every matching row.** *i.e.*, a full-library download in one call.

---

## TUI

```bash
uv venv venv
source venv/bin/activate
uv pip install psycopg[binary] pillow textual textual-image
PG_DSN=postgresql://pgstore:pgstore-dev@localhost:5432/pgstore python app.py
```

Arrow keys `/` click to select, `r` for refresh, `q` to quit. The TUI reads image blobs directly over SQL and renders previews with Pillow. Non-image payloads show a preview error.

---

## Lessons learnt

> *(the hard way)*

1. Upload only through `/rpc/store_objects`. A plain `POST /objects` with a base64 string stores the text characters, not the decoded image. Resulting in silent double-encoding and roughly 33% size inflation. The RPC decodes and checksums the bytes.
2. PostgREST returns `bytea` as Postgres hex (`\x...`), not base64. Clients must hex-decode not base64-decode.
3. Content types are trusted, not validated. Currently, the store will file a WebP as `image/jpeg`. Need to verify the image bytes.
4. Base64 inflates every upload by roughly 33% and is not streamed end-to-end. Fine for small-to-medium images, but a known limit of the gateway and a reason for the FastAPI roadmap item.
5. The anonymous role is a superuser. This stack is for local use. Do not expose PostgREST or Postgres to a network without adding authentication and a least-privilege route.

---

## Roadmap

- [] FastAPI service to replace the PostgREST: raw binary upload and download (multipart, streaming) with `index/html` served from the application. PostgREST is a working placeholder, not a long-term API. Do not build integrations against `rpc/store_object` that cannot be migrated.
- [] Magic-byte validation against the declared `content_type`.
- [] `uv` dependency management and `app.py` containerisation.
- [] Benchmarks: `bytea` vs `pg_largeobjects` at at least 50 and 200Mb payload sizes.

---

## License

GPL-3.0. See [LICENSE](LICENSE).
