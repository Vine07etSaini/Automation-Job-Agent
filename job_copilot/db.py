"""SQLite application tracker."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from job_copilot.config import DB_PATH, IST, ensure_dirs
from job_copilot.models import JobPosting, JobStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    fingerprint      TEXT NOT NULL,
    portal           TEXT NOT NULL,
    external_id      TEXT NOT NULL,
    title            TEXT NOT NULL,
    company          TEXT NOT NULL,
    location         TEXT,
    url              TEXT NOT NULL,
    experience_text  TEXT,
    salary_text      TEXT,
    posted_at        TEXT,
    description      TEXT,
    recruiter_email  TEXT,
    recruiter_phone  TEXT,
    analysis_json    TEXT,
    match_score      INTEGER,
    match_json       TEXT,
    resume_path      TEXT,
    resume_url       TEXT,
    guard_json       TEXT,
    status           TEXT NOT NULL,
    status_reason    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    applied_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_fingerprint ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

JSON_COLUMNS = ("analysis_json", "match_json", "guard_json")


def now_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def job_id(portal: str, external_id: str) -> str:
    return hashlib.sha1(f"{portal}:{external_id}".encode()).hexdigest()[:12]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def fingerprint(company: str, title: str) -> str:
    """Portal-independent key used to de-duplicate the same job across portals."""
    return hashlib.sha1(f"{_norm(company)}|{_norm(title)}".encode()).hexdigest()[:16]


class Tracker:
    def __init__(self, path: Path = DB_PATH):
        ensure_dirs()
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ writes

    def is_known(self, posting: JobPosting) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM jobs WHERE id = ? OR fingerprint = ? LIMIT 1",
                (job_id(posting.portal, posting.external_id), fingerprint(posting.company, posting.title)),
            ).fetchone()
        return row is not None

    def upsert_job(self, posting: JobPosting, status: JobStatus, reason: str = "", **fields: Any) -> str:
        jid = job_id(posting.portal, posting.external_id)
        ts = now_iso()
        row = {
            "id": jid,
            "fingerprint": fingerprint(posting.company, posting.title),
            "portal": posting.portal,
            "external_id": posting.external_id,
            "title": posting.title,
            "company": posting.company,
            "location": posting.location,
            "url": posting.url,
            "experience_text": posting.experience_text,
            "salary_text": posting.salary_text,
            "posted_at": posting.posted_at.isoformat() if posting.posted_at else None,
            "description": posting.description,
            "recruiter_email": posting.recruiter_email,
            "recruiter_phone": posting.recruiter_phone,
            "status": str(status),
            "status_reason": reason,
            "created_at": ts,
            "updated_at": ts,
            **{k: _encode(v) for k, v in fields.items()},
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k not in ("id", "created_at"))
        with self._conn() as c:
            c.execute(
                f"INSERT INTO jobs ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}",
                list(row.values()),
            )
        return jid

    def update(self, jid: str, **fields: Any) -> None:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id = ?", [_encode(v) for v in fields.values()] + [jid])

    def set_status(self, jid: str, status: JobStatus, reason: str = "") -> None:
        extra = {"applied_at": now_iso()} if status == JobStatus.APPLIED else {}
        self.update(jid, status=status, status_reason=reason, **extra)

    # ------------------------------------------------------------ reads

    def get(self, jid: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
        return _decode(row) if row else None

    def list(self, status: str | None = None, since: str | None = None, limit: int = 100) -> list[dict]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if since:
            sql += " AND updated_at >= ?"
            args.append(since)
        sql += " ORDER BY match_score IS NULL, match_score DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [_decode(r) for r in c.execute(sql, args).fetchall()]

    def applied_count_today(self, portal: str | None = None) -> int:
        today = datetime.now(IST).date().isoformat()
        sql = "SELECT COUNT(*) FROM jobs WHERE status = ? AND applied_at >= ?"
        args: list = [str(JobStatus.APPLIED), today]
        if portal:
            sql += " AND portal = ?"
            args.append(portal)
        with self._conn() as c:
            return c.execute(sql, args).fetchone()[0]


def _encode(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return json.dumps(v.model_dump(mode="json"))
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, JobStatus):
        return str(v)
    return v


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in JSON_COLUMNS:
        raw = d.pop(k, None)
        d[k.removesuffix("_json")] = json.loads(raw) if raw else None
    return d
