import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

DB_PATH = os.environ.get("DB_PATH", "data/jobwatch.db")

# status: queued (in RabbitMQ, waiting for verdict) | skipped (below threshold) | yes | no
SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL, ext_id TEXT NOT NULL,
  title TEXT, company TEXT, url TEXT, salary TEXT, text TEXT,
  score REAL, status TEXT NOT NULL,
  found_at TEXT DEFAULT CURRENT_TIMESTAMP, decided_at TEXT,
  UNIQUE (source, ext_id)
);
CREATE TABLE IF NOT EXISTS cursor (key TEXT PRIMARY KEY, value INTEGER);
"""


@dataclass
class Vacancy:
    source: str
    ext_id: str
    title: str
    url: str
    company: str = ""
    salary: str = ""
    text: str = ""
    id: int = 0
    score: float = 0
    status: str = ""


# ponytail: sync sqlite3 called from async code; calls are ~1ms, aiosqlite if that ever hurts
@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with c:  # commit / rollback
            yield c
    finally:
        c.close()


def init():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")  # collector and bot share the file
        c.executescript(SCHEMA)
        try:  # migration for databases created before fingerprints
            c.execute("ALTER TABLE vacancies ADD COLUMN fp TEXT")
        except sqlite3.OperationalError:
            pass
        c.execute("CREATE INDEX IF NOT EXISTS vacancies_fp ON vacancies (fp)")


def fingerprint(v: "Vacancy") -> str:
    """Same post re-published in several channels -> same fingerprint.
    Hashtags, mentions and links differ between channels, so they are dropped.
    Telegram 'company' is the channel name, so it is left out there; elsewhere it tells
    apart two 'Python Developer' vacancies with the same skill list."""
    raw = v.text if v.source == "telegram" else f"{v.title}|{v.company}|{v.text}"
    raw = re.sub(r"https?://\S+|[#@]\w+", "", raw.lower())
    return hashlib.sha1(re.sub(r"\W+", "", raw)[:300].encode()).hexdigest()


def _vac(r) -> Vacancy:
    return Vacancy(**{k: r[k] for k in Vacancy.__dataclass_fields__})


def add(v: Vacancy, score: float, status: str) -> int | None:
    """Insert unless (source, ext_id) or the same content was seen before. Returns row id, None for a duplicate."""
    fp = fingerprint(v)
    with conn() as c:
        if c.execute("SELECT 1 FROM vacancies WHERE fp=?", (fp,)).fetchone():
            return None
        cur = c.execute(
            "INSERT OR IGNORE INTO vacancies (source, ext_id, title, company, url, salary, text, score, status, fp)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (v.source, v.ext_id, v.title, v.company, v.url, v.salary, v.text, score, status, fp),
        )
        return cur.lastrowid if cur.rowcount else None


def seen(source: str, ext_id: str) -> bool:
    with conn() as c:
        return c.execute("SELECT 1 FROM vacancies WHERE source=? AND ext_id=?", (source, ext_id)).fetchone() is not None


def get(vid: int) -> Vacancy | None:
    with conn() as c:
        r = c.execute("SELECT * FROM vacancies WHERE id=?", (vid,)).fetchone()
    return _vac(r) if r else None


def set_status(vid: int, status: str, only_from: str | None = None) -> bool:
    with conn() as c:
        cur = c.execute(
            "UPDATE vacancies SET status=?,"
            " decided_at=CASE WHEN ? IN ('yes','no') THEN CURRENT_TIMESTAMP END"
            " WHERE id=? AND (? IS NULL OR status=?)",
            (status, status, vid, only_from, only_from),
        )
        return cur.rowcount > 0


def delete(vid: int):
    with conn() as c:
        c.execute("DELETE FROM vacancies WHERE id=?", (vid,))


def listing(status: str, limit: int) -> list[Vacancy]:
    order = "score DESC, id DESC" if status == "skipped" else "decided_at DESC, id DESC"
    with conn() as c:
        rows = c.execute(
            f"SELECT * FROM vacancies WHERE status=? ORDER BY {order} LIMIT ?", (status, limit)
        ).fetchall()
    return [_vac(r) for r in rows]


def counts() -> dict[str, int]:
    with conn() as c:
        return {r[0]: r[1] for r in c.execute("SELECT status, COUNT(*) FROM vacancies GROUP BY status")}


def cursor_get(key: str) -> int | None:
    with conn() as c:
        r = c.execute("SELECT value FROM cursor WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def cursor_set(key: str, value: int):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO cursor (key, value) VALUES (?,?)", (key, value))


def cursors(prefix: str) -> dict[str, int]:
    with conn() as c:
        rows = c.execute("SELECT key, value FROM cursor WHERE key LIKE ? ORDER BY key", (prefix + "%",)).fetchall()
    return {k[len(prefix):]: v for k, v in rows}
