-- Object storage in one Postgres instance.
-- Objects stored as bytea; TOAST handles compression + out-of-line storage.

CREATE TYPE object_status AS ENUM ('pending', 'stored', 'failed');

-- Bucket-ish abstraction so multiple "stores" can share the instance.
CREATE TABLE buckets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Binary object: the file itself, plus enough metadata to serve it.
CREATE TABLE objects (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id    uuid NOT NULL REFERENCES buckets(id) ON DELETE CASCADE,
    key          text NOT NULL,              -- "uploads/example/page1.jpg"
    content_type text NOT NULL DEFAULT 'application/octet-stream',
    size_bytes   bigint NOT NULL,
    checksum_sha256 text,                     -- integrity check
    status       object_status NOT NULL DEFAULT 'pending',
    data         bytea NOT NULL,              -- the payload (TOASTed)
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bucket_id, key)                   -- S3-style uniqueness
);

CREATE INDEX objects_by_bucket_idx ON objects (bucket_id, created_at DESC);

-- Housekeeping function: auto-set size + updated_at, no app-side bookkeeping.
CREATE OR REPLACE FUNCTION objects_touch() RETURNS trigger AS $$
BEGIN
    NEW.size_bytes := octet_length(NEW.data);
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER objects_touch_trg
    BEFORE INSERT OR UPDATE OF data ON objects
    FOR EACH ROW EXECUTE FUNCTION objects_touch();

-- Seed a default bucket so you can insert immediately.
INSERT INTO buckets (name) VALUES ('default');

-- Upload endpoint used by index.html: POST /rpc/store_object
-- Takes a base64 payload (JSON-friendly), upserts by (bucket, key), returns metadata only
-- (no bytea echo). size_bytes + updated_at are set by objects_touch_trg.
CREATE OR REPLACE FUNCTION store_object(
    p_bucket text,
    p_key    text,
    p_ct     text DEFAULT 'application/octet-stream',
    p_b64    text DEFAULT NULL
) RETURNS TABLE (
    id              uuid,
    bucket_id       uuid,
    key             text,
    content_type    text,
    size_bytes      bigint,
    checksum_sha256 text,
    status          object_status,
    created_at      timestamptz,
    updated_at      timestamptz
) AS $$
#variable_conflict use_column
DECLARE
    v_bucket uuid;
    v_data   bytea;
BEGIN
    IF p_key IS NULL OR btrim(p_key) = '' THEN
        RAISE EXCEPTION 'object key must not be empty' USING ERRCODE = '22023';
    END IF;
    IF p_b64 IS NULL THEN
        RAISE EXCEPTION 'p_b64 (base64 payload) is required' USING ERRCODE = '22023';
    END IF;

    SELECT b.id INTO v_bucket FROM buckets b WHERE b.name = p_bucket;
    IF v_bucket IS NULL THEN
        RAISE EXCEPTION 'bucket "%" does not exist', p_bucket USING ERRCODE = 'P0002';
    END IF;

    v_data := decode(p_b64, 'base64');

    RETURN QUERY
    INSERT INTO objects AS o (bucket_id, key, content_type, size_bytes, checksum_sha256, status, data)
    VALUES (v_bucket, p_key, COALESCE(NULLIF(p_ct, ''), 'application/octet-stream'),
            octet_length(v_data), encode(sha256(v_data), 'hex'), 'stored', v_data)
    ON CONFLICT (bucket_id, key) DO UPDATE
        SET content_type    = EXCLUDED.content_type,
            checksum_sha256 = EXCLUDED.checksum_sha256,
            status          = 'stored',
            data            = EXCLUDED.data
    RETURNING o.id, o.bucket_id, o.key, o.content_type, o.size_bytes,
              o.checksum_sha256, o.status, o.created_at, o.updated_at;
END;
$$ LANGUAGE plpgsql;
