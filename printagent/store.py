"""SQLite persistence for preflight results and draft orders."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("PRINTAGENT_DB", Path.home() / ".printagent" / "printagent.db"))

SCHEMA = """
create table if not exists preflights (
  id text primary key, created_at text, file text, sha256 text,
  product_id text, status text, report text);
create table if not exists orders (
  id text primary key, created_at text, status text, product_id text,
  quote text, front_preflight text, back_preflight text, note text);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_preflight(report: dict) -> str:
    pid = "pf_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("insert into preflights values (?,?,?,?,?,?,?)",
                  (pid, _now(), report["file"], file_hash(report["file"]),
                   report["product_id"], report["status"], json.dumps(report, default=str)))
    return pid


def get_preflight(pid: str) -> sqlite3.Row:
    with _conn() as c:
        row = c.execute("select * from preflights where id=?", (pid,)).fetchone()
    if not row:
        raise ValueError(f"Unknown preflight id '{pid}'. Run preflight_artwork first.")
    return row


def create_order(product_id: str, quote: dict, front: str, back: str | None, note: str) -> dict:
    """Gate: every file must have passed preflight for THIS product and be unchanged since."""
    for pid in filter(None, [front, back]):
        row = get_preflight(pid)
        if row["product_id"] != product_id:
            raise ValueError(f"{pid} was checked for '{row['product_id']}', not '{product_id}'.")
        if row["status"] == "fail":
            raise ValueError(f"{pid} failed preflight. Fix the artwork (fix_artwork) and re-check.")
        if not Path(row["file"]).exists() or file_hash(row["file"]) != row["sha256"]:
            raise ValueError(f"The file for {pid} changed after it was checked. Run preflight again.")
    oid = "HP-" + uuid.uuid4().hex[:8].upper()
    with _conn() as c:
        c.execute("insert into orders values (?,?,?,?,?,?,?,?)",
                  (oid, _now(), "draft", product_id, json.dumps(quote), front, back, note))
    return get_order(oid)


def get_order(oid: str) -> dict:
    with _conn() as c:
        row = c.execute("select * from orders where id=?", (oid,)).fetchone()
    if not row:
        raise ValueError(f"Unknown order '{oid}'.")
    d = dict(row)
    d["quote"] = json.loads(d["quote"])
    return d
