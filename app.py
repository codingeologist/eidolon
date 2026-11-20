"""
Postgres Object Store browser (terminal UI).

Lists objects from the `objects` / `buckets` tables and previews images.
Connection string: PG_DSN env var (default matches docker-compose.yml).
"""
from __future__ import annotations

import io
import os
from typing import Optional

import psycopg
from PIL import Image as PILImage
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static
from textual_image.widget import Image as TImage


PG_DSN = os.environ.get("PG_DSN", "postgresql://pgstore:pgstore-dev@localhost:5432/pgstore")
PAGE_SIZE = 50

LIST_SQL = """
SELECT o.id, b.name, o.key, o.content_type, o.size_bytes, o.created_at,
       o.checksum_sha256, o.status
FROM objects o
JOIN buckets b ON b.id = o.bucket_id
ORDER BY b.name, o.key
LIMIT %s OFFSET %s
"""

BLOB_SQL = "SELECT data FROM objects WHERE id = %s"

# --------------------------------------------------------------------------
# Data layer (sync psycopg; swap for a pool if this grows)
# --------------------------------------------------------------------------

def fetch_rows(conn: psycopg.Connection, limit: int, offset: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(LIST_SQL, (limit, offset))
        return cur.fetchall()


def fetch_blob(conn: psycopg.Connection, object_id) -> Optional[PILImage.Image]:
    with conn.cursor() as cur:
        cur.execute(BLOB_SQL, (object_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return PILImage.open(io.BytesIO(bytes(row[0]))).convert("RGB")


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

class ObjectTable(DataTable):
    """Bucket, key, type, size, created. Enter or click to preview."""

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns("Bucket", "Key", "Type", "Bytes", "Created")


class MetaPanel(Static):
    """Metadata summary for the selected object."""


class ObjectBrowserApp(App[None]):
    TITLE = "Postgres Object Store Browser"
    CSS = """
    #body { height: 1fr; }
    #table-pane { width: 60%; }
    #detail-pane { width: 40%; }
    #image-pane { height: 1fr; border: round $primary; }
    MetaPanel { height: auto; padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._conn: psycopg.Connection | None = None
        self._offset = 0
        self._meta: dict[str, tuple] = {}  # row key (object id) -> full row

    # ---- lifecycle ----

    def on_mount(self) -> None:
        self._bootstrap()

    def _bootstrap(self) -> None:
        try:
            self._conn = psycopg.connect(PG_DSN, autocommit=True)
            self._load_page()
        except Exception as exc:  # surface DB errors instead of dying silently
            self.query_one("#image-pane", Static).update(f"[b red]DB error:[/] {exc}")

    def _load_page(self) -> None:
        assert self._conn is not None
        table = self.query_one(ObjectTable)
        table.clear()
        self._meta.clear()
        for row in fetch_rows(self._conn, PAGE_SIZE, self._offset):
            object_id, bucket, key, ctype, size, created, *_ = row
            self._meta[str(object_id)] = row
            table.add_row(bucket, key, ctype, str(size), f"{created:%Y-%m-%d %H:%M}",
                          key=str(object_id))

    # ---- events ----

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        object_id = str(event.row_key.value)
        row = self._meta.get(object_id)
        if row is not None:
            _, bucket, key, ctype, size, created, checksum, status = row
            self.query_one(MetaPanel).update(
                f"id:       {object_id}\nbucket:   {bucket}\nkey:      {key}\n"
                f"type:     {ctype}\nsize:     {size} bytes\nstatus:   {status}\n"
                f"sha256:   {checksum}\ncreated:  {created}"
            )
        self._show_blob(object_id)

    def _show_blob(self, object_id: str) -> None:
        pane = self.query_one("#image-pane", Static)
        pane.update("loading…")
        try:
            img = fetch_blob(self._conn, object_id)
        except Exception as exc:  # e.g. not an image
            pane.update(f"[b red]cannot preview:[/] {exc}")
            return
        if img is None:
            pane.update("object not found")
            return
        for w in pane.query(TImage):
            w.remove()
        pane.update("")
        pane.mount(TImage(img))

    # ---- bindings ----

    def action_refresh(self) -> None:
        self._load_page()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="table-pane"):
                yield ObjectTable()
            with Vertical(id="detail-pane"):
                yield Static("Metadata", classes="label")
                yield MetaPanel("")
                yield Static("Preview", classes="label")
                yield Static(id="image-pane")
        yield Footer()


if __name__ == "__main__":
    ObjectBrowserApp().run()
