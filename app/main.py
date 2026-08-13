"""Task 3 - mini audio collection app, plus the HTTP surface the n8n flow (Task 2)
talks to.

    uvicorn app.main:app --reload --port 8000

Routes
    GET  /                            record / upload page
    GET  /submissions                 list view with players + extracted props
    POST /api/submissions             multipart: name, phone, audio
    GET  /api/audio/{id}              stream a stored file
    GET  /api/people                  people list (filters for the n8n flow)
    PATCH /api/people/{id}/skill-category   n8n LLM write-back
    POST /api/match/check             duplicate check used by the n8n flow
    GET  /api/stats                   pipeline + submission counters
"""
from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.audio_features import extract  # noqa: E402
from src.db import DB_PATH, connect, init_db  # noqa: E402
from src.normalize import normalize_name, normalize_phone, phone_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = ROOT / "data" / "audio"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024          # 25 MB - a 5 min voice clip is ~2 MB
ALLOWED_SUFFIXES = {".wav", ".webm", ".ogg", ".oga", ".mp3", ".m4a", ".mp4", ".flac", ".aac", ".opus"}

app = FastAPI(title="ConsultBae Audio Collection", version="1.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


def db() -> sqlite3.Connection:
    conn = connect(DB_PATH)
    init_db(conn)          # no-op when the pipeline already built it
    return conn


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def page_record(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/submissions", response_class=HTMLResponse)
def page_submissions(request: Request):
    conn = db()
    rows = conn.execute(
        """SELECT s.*, p.full_name AS person_name, p.city AS person_city,
                  p.skill_category
             FROM audio_submission s
             LEFT JOIN person p ON p.person_id = s.person_id
            ORDER BY s.submission_id DESC"""
    ).fetchall()
    stats = conn.execute(
        """SELECT COUNT(*) n,
                  COALESCE(ROUND(SUM(duration_sec)/60.0, 1), 0) minutes,
                  COALESCE(SUM(person_id IS NOT NULL), 0) linked,
                  COALESCE(SUM(quality_label='good'), 0) good
             FROM audio_submission"""
    ).fetchone()
    conn.close()
    return templates.TemplateResponse(
        request, "submissions.html", {"rows": rows, "stats": stats}
    )


# --------------------------------------------------------------------------
# submission
# --------------------------------------------------------------------------
@app.post("/api/submissions")
async def create_submission(
    name: str = Form(...),
    phone: str = Form(...),
    audio: UploadFile = File(...),
):
    nm = normalize_name(name)
    ph = normalize_phone(phone)
    if not nm.value:
        raise HTTPException(400, "name is required")
    if not ph.value:
        raise HTTPException(400, "phone must be a 10-digit Indian mobile (+91 / 0 prefixes are fine)")

    suffix = Path(audio.filename or "").suffix.lower() or ".webm"
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported audio type '{suffix}'")

    stored_name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}{suffix}"
    dest = MEDIA_DIR / stored_name

    # stream to disk with a hard size cap, hashing as we go
    sha = hashlib.sha256()
    size = 0
    with dest.open("wb") as fh:
        while chunk := await audio.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"file exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB limit")
            sha.update(chunk)
            fh.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "empty audio file")

    props = extract(dest)
    if props.quality_label == "unreadable":
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "could not decode that audio file")

    conn = db()
    cur = conn.cursor()

    # duplicate submission: same person sending the identical file twice
    digest = sha.hexdigest()
    dup = cur.execute("SELECT submission_id FROM audio_submission WHERE sha256 = ?", (digest,)).fetchone()

    person_id, matched_by = _resolve_person(cur, nm.value, ph.value)

    cur.execute(
        """INSERT INTO audio_submission (person_id, submitted_name, submitted_phone, matched_by,
                file_name, stored_path, mime_type, file_size_bytes, sha256,
                duration_sec, sample_rate_khz, bitrate_kbps, loudness_db,
                peak_db, est_snr_db, clipping_pct, silence_pct, zcr_mean,
                spectral_flatness, quality_label, quality_notes, channels, codec)
           VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)""",
        (
            person_id, nm.value, ph.value, matched_by,
            audio.filename or stored_name, str(dest.relative_to(ROOT)),
            audio.content_type, size, digest,
            props.duration_sec, props.sample_rate_khz, props.bitrate_kbps, props.loudness_db,
            props.peak_db, props.est_snr_db, props.clipping_pct, props.silence_pct, props.zcr_mean,
            props.spectral_flatness, props.quality_label, props.quality_notes,
            props.channels, props.codec,
        ),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()

    return JSONResponse(
        {
            "submission_id": sid,
            "person_id": person_id,
            "matched_by": matched_by,
            "duplicate_of": dup["submission_id"] if dup else None,
            "properties": props.as_dict(),
        }
    )


def _resolve_person(cur: sqlite3.Cursor, name: str, phone: str) -> tuple[int | None, str]:
    """Link a submission to the merged database using the same phone key the
    pipeline used - so an existing applicant does NOT become a second person."""
    key = phone_key(phone)
    row = cur.execute(
        """SELECT p.person_id FROM person p
             JOIN person_phone pp ON pp.person_id = p.person_id
            WHERE replace(replace(pp.phone,'+',''),' ','') LIKE ?
            LIMIT 1""",
        (f"%{key}",),
    ).fetchone()
    if row:
        return row["person_id"], "phone"

    cur.execute(
        """INSERT INTO person (full_name, primary_phone, source_count, match_methods, match_confidence)
           VALUES (?,?,0,'audio_app',1.0)""",
        (name, phone),
    )
    pid = cur.lastrowid
    cur.execute(
        "INSERT OR IGNORE INTO person_phone (person_id, phone, source, is_primary) VALUES (?,?,?,1)",
        (pid, phone, "audio_app"),
    )
    cur.execute(
        "INSERT OR IGNORE INTO person_name_alias (person_id, name, source) VALUES (?,?,?)",
        (pid, name, "audio_app"),
    )
    return pid, "new_person"


@app.get("/api/audio/{submission_id}")
def get_audio(submission_id: int):
    conn = db()
    row = conn.execute(
        "SELECT stored_path, mime_type, file_name FROM audio_submission WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "no such submission")
    path = ROOT / row["stored_path"]
    if not path.exists():
        raise HTTPException(410, "file missing from storage")
    return FileResponse(path, media_type=row["mime_type"] or "application/octet-stream")


@app.get("/api/submissions")
def list_submissions(limit: int = Query(100, le=1000)):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM audio_submission ORDER BY submission_id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# endpoints consumed by the n8n workflow (Task 2)
# --------------------------------------------------------------------------
@app.get("/api/people")
def list_people(
    untagged: bool = Query(False, description="only people with no skill_category yet"),
    limit: int = Query(200, le=1000),
):
    conn = db()
    sql = "SELECT person_id, full_name, primary_email, city, skills, skill_category FROM v_person_full"
    if untagged:
        sql += " WHERE skill_category IS NULL AND skills IS NOT NULL"
    sql += " ORDER BY person_id LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


class CategoryIn(BaseModel):
    skill_category: str
    confidence: float | None = None
    tagged_by: str | None = "n8n-llm"


VALID_CATEGORIES = {"automation-heavy", "web-dev", "data", "generalist"}


@app.patch("/api/people/{person_id}/skill-category")
def set_category(person_id: int, body: CategoryIn):
    cat = body.skill_category.strip().lower()
    if cat not in VALID_CATEGORIES:
        raise HTTPException(422, f"skill_category must be one of {sorted(VALID_CATEGORIES)}")
    conn = db()
    cur = conn.execute(
        """UPDATE person
              SET skill_category = ?, skill_category_conf = ?, skill_category_by = ?,
                  skill_category_at = datetime('now'), updated_at = datetime('now')
            WHERE person_id = ?""",
        (cat, body.confidence, body.tagged_by, person_id),
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        raise HTTPException(404, "no such person")
    return {"person_id": person_id, "skill_category": cat, "confidence": body.confidence}


class MatchIn(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


@app.post("/api/match/check")
def match_check(body: MatchIn):
    """Same three-tier logic the pipeline uses, exposed for the n8n duplicate check."""
    conn = db()
    hits: list[dict] = []
    if body.email:
        r = conn.execute(
            """SELECT p.person_id, p.full_name, 'email' AS matched_on, 1.0 AS confidence
                 FROM person p JOIN person_email pe ON pe.person_id = p.person_id
                WHERE lower(pe.email) = lower(?)""",
            (body.email.strip(),),
        ).fetchall()
        hits += [dict(x) for x in r]
    if not hits and body.phone:
        key = phone_key(body.phone)
        if key:
            r = conn.execute(
                """SELECT p.person_id, p.full_name, 'phone' AS matched_on, 0.99 AS confidence
                     FROM person p JOIN person_phone pp ON pp.person_id = p.person_id
                    WHERE pp.phone LIKE ?""",
                (f"%{key}",),
            ).fetchall()
            hits += [dict(x) for x in r]
    if not hits and body.name:
        r = conn.execute(
            "SELECT person_id, full_name, 'name' AS matched_on, 0.5 AS confidence FROM person WHERE lower(full_name) = lower(?)",
            (body.name.strip(),),
        ).fetchall()
        hits += [dict(x) for x in r]
    conn.close()
    return {"is_duplicate": bool(hits), "matches": hits}


@app.get("/api/stats")
def stats():
    conn = db()
    q = lambda sql: conn.execute(sql).fetchone()[0]
    out = {
        "people": q("SELECT COUNT(*) FROM person"),
        "people_in_all_three_sources": q("SELECT COUNT(*) FROM person WHERE in_source1+in_source2+in_source3=3"),
        "data_issues": q("SELECT COUNT(*) FROM data_issue"),
        "pending_reviews": q("SELECT COUNT(*) FROM match_review WHERE resolved=0"),
        "submissions": q("SELECT COUNT(*) FROM audio_submission"),
        "tagged": q("SELECT COUNT(*) FROM person WHERE skill_category IS NOT NULL"),
    }
    conn.close()
    return out


@app.get("/healthz")
def healthz():
    return {"ok": True, "db": DB_PATH.exists(), "ffmpeg": bool(shutil.which("ffmpeg"))}
