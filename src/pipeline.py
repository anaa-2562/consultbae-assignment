"""Task 1 entrypoint: read 3 CSVs -> resolve identities -> one clean SQLite DB.

    python -m src.pipeline            # rebuild data/consultbae.db
    python -m src.pipeline --report   # rebuild and print the summary

The pipeline is a full rebuild and is idempotent: running it twice produces a
byte-identical set of rows (nothing depends on wall-clock ordering).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from .db import DB_PATH, reset_db
from .ingest import READERS, ParsedRow, raw_json
from .matching import Record, resolve
from rapidfuzz import fuzz

from .normalize import Issue, dedupe, name_key

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"

# Which source is authoritative for which field when a person exists in several.
FIELD_OWNER = {
    "experience_years": "source1",
    "current_ctc_inr": "source1",
    "ctc_source_unit": "source1",
    "applied_date": "source1",
    "gig_rate_inr_hour": "source2",
    "gig_rate_inr_month": "source2",
    "gig_rate_unit": "source2",
    "gig_status": "source2",
    "cbnexus_verified": "source3",
    "projects_completed": "source3",
}
# Preference order when picking a display name / city / contact details.
SOURCE_PRIORITY = ["source1", "source3", "source2"]


def _pick_name(members: list[Record]) -> tuple[str, list[Issue]]:
    """Prefer the most complete spelling: most tokens, then source priority.
    `R. Verma` loses to `Rohit Verma`; ALL CAPS loses to Title Case (already
    normalised upstream)."""
    issues: list[Issue] = []
    variants = dedupe([m.name for m in members if m.name])
    if len(variants) > 1:
        issues.append(
            Issue(
                "NAME_VARIANTS_MERGED",
                "full_name",
                " | ".join(variants),
                "kept the most complete spelling, others stored as aliases",
                severity="low",
            )
        )
    ranked = sorted(
        members,
        key=lambda m: (
            -len([t for t in name_key(m.name).split() if len(t) > 1]),
            -len(m.name),
            SOURCE_PRIORITY.index(m.source),
        ),
    )
    return (ranked[0].name if ranked else ""), issues


def _pick_email(members: list[Record], display_name: str) -> tuple[str | None, list[Issue]]:
    """When one person owns several mailboxes, promote the one that looks like
    their real address rather than the first one encountered.

    `alt.nikhil.chopra70@example.com` and `nikhil.chopra70@example.com` are the
    same human (identical phone). The local part with the highest similarity to
    the person's name - shortest wins ties - becomes primary; the other is kept
    in `person_email` so inbound mail on either address still resolves.
    """
    issues: list[Issue] = []
    candidates = [(m, m.email) for m in members if m.email]
    if not candidates:
        return None, issues
    key = name_key(display_name).replace(" ", "")

    def score(item):
        m, email = item
        local = email.split("@")[0]
        stripped = "".join(ch for ch in local.lower() if ch.isalpha())
        return (
            -fuzz.ratio(key, stripped),
            len(local),
            SOURCE_PRIORITY.index(m.source),
        )

    best = sorted(candidates, key=score)[0][1]
    others = [e for _, e in candidates if e != best]
    if others:
        issues.append(
            Issue(
                "ALTERNATE_EMAIL",
                "primary_email",
                " | ".join([best] + others),
                f"'{best}' promoted to primary (closest match to the person's name); alternates retained",
                best,
                "low",
            )
        )
    return best, issues


def _pick_city(members: list[Record]) -> tuple[str | None, list[Issue]]:
    issues: list[Issue] = []
    cities = [m.city for m in members if m.city]
    if not cities:
        return None, issues
    distinct = dedupe(cities)
    if len(distinct) > 1:
        counts = Counter(cities)
        winner = sorted(distinct, key=lambda c: (-counts[c], SOURCE_PRIORITY.index(next(m.source for m in members if m.city == c))))[0]
        issues.append(
            Issue(
                "CROSS_SOURCE_CITY_CONFLICT",
                "city",
                " | ".join(f"{m.source}={m.city}" for m in members if m.city),
                f"majority vote -> {winner} (ties broken by source priority)",
                winner,
                "medium",
            )
        )
        return winner, issues
    return distinct[0], issues


def build(db_path: Path | None = None, verbose: bool = True) -> dict:
    conn = reset_db(db_path or DB_PATH)
    cur = conn.cursor()

    parsed_rows: list[tuple[str, ParsedRow]] = []
    records: list[Record] = []

    # ---------------- ingest -------------------------------------------------
    for filename, reader in READERS.items():
        path = RAW_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"missing raw file: {path}")
        for pr in reader(path):
            parsed_rows.append((filename, pr))
            if pr.record is not None:
                records.append(pr.record)

    # ---------------- resolve identities ------------------------------------
    result = resolve(records)
    rid_to_person: dict[str, int] = {}

    # ---------------- write golden records ----------------------------------
    merge_issues: list[tuple[str, int | None, Issue]] = []
    for cluster in result.clusters:
        name, name_issues = _pick_name(cluster)
        city, city_issues = _pick_city(cluster)
        by_source = {m.source: m for m in cluster}

        def owned(field_: str):
            src = FIELD_OWNER[field_]
            m = by_source.get(src)
            return m.payload.get(field_) if m else None

        emails = dedupe([m.email for m in cluster if m.email])
        phones = dedupe([m.phone for m in cluster if m.phone])
        primary_email, email_issues = _pick_email(cluster, name)
        primary_phone = next(
            (m.phone for s in SOURCE_PRIORITY for m in cluster if m.source == s and m.phone), None
        )
        root_methods = result.methods.get(cluster[0].rid, set())
        if not root_methods:
            # cluster root may be keyed on another member
            for m in cluster:
                if m.rid in result.methods:
                    root_methods = result.methods[m.rid]
                    break
        confidence = 1.0 if len(cluster) == 1 else min(
            {"email": 1.0, "phone": 0.99, "name_city": 0.80}[m] for m in root_methods
        ) if root_methods else 1.0

        cur.execute(
            """INSERT INTO person (full_name, primary_email, primary_phone, city,
                    experience_years, current_ctc_inr, ctc_source_unit, applied_date,
                    gig_rate_inr_hour, gig_rate_inr_month, gig_rate_unit, gig_status,
                    cbnexus_verified, projects_completed,
                    source_count, in_source1, in_source2, in_source3,
                    match_methods, match_confidence)
               VALUES (?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?, ?,?,?,?, ?,?)""",
            (
                name, primary_email, primary_phone, city,
                owned("experience_years"), owned("current_ctc_inr"),
                owned("ctc_source_unit"), owned("applied_date"),
                owned("gig_rate_inr_hour"), owned("gig_rate_inr_month"),
                owned("gig_rate_unit"), owned("gig_status"),
                (None if owned("cbnexus_verified") is None else int(owned("cbnexus_verified"))),
                owned("projects_completed"),
                len({m.source for m in cluster}),
                int("source1" in by_source), int("source2" in by_source), int("source3" in by_source),
                ",".join(sorted(root_methods)) or "single_source",
                confidence,
            ),
        )
        pid = cur.lastrowid
        for m in cluster:
            rid_to_person[m.rid] = pid

        for e in emails:
            cur.execute(
                "INSERT OR IGNORE INTO person_email (person_id, email, source, is_primary) VALUES (?,?,?,?)",
                (pid, e, next(m.source for m in cluster if m.email == e), int(e == primary_email)),
            )
        for p in phones:
            cur.execute(
                "INSERT OR IGNORE INTO person_phone (person_id, phone, source, is_primary) VALUES (?,?,?,?)",
                (pid, p, next(m.source for m in cluster if m.phone == p), int(p == primary_phone)),
            )
        for m in cluster:
            cur.execute(
                "INSERT OR IGNORE INTO person_name_alias (person_id, name, source) VALUES (?,?,?)",
                (pid, m.payload.get("raw_name") or m.name, m.source),
            )
        for m in cluster:
            for s in m.payload.get("skills", []) or []:
                cur.execute("INSERT OR IGNORE INTO skill (name) VALUES (?)", (s,))
                sid = cur.execute("SELECT skill_id FROM skill WHERE name = ?", (s,)).fetchone()[0]
                cur.execute(
                    "INSERT OR IGNORE INTO person_skill (person_id, skill_id, source) VALUES (?,?,?)",
                    (pid, sid, m.source),
                )

        # cross-source consistency checks worth reporting
        anchor = cluster[0]
        for iss in name_issues + city_issues + email_issues:
            merge_issues.append((anchor.source, anchor.row, iss))
        if len(emails) > 1:
            merge_issues.append(
                (
                    anchor.source,
                    anchor.row,
                    Issue(
                        "MULTIPLE_EMAILS_ONE_PERSON",
                        "email",
                        " | ".join(emails),
                        "same phone proves one human with two mailboxes; both kept in person_email",
                        primary_email,
                        "high",
                    ),
                )
            )
        s1s2 = [m for m in cluster if m.source in ("source1", "source2") and m.payload.get("skills")]
        if len(s1s2) == 2:
            a, b = ({*s1s2[0].payload["skills"]}, {*s1s2[1].payload["skills"]})
            if a != b:
                merge_issues.append(
                    (
                        anchor.source,
                        anchor.row,
                        Issue(
                            "SKILL_SET_DIVERGENCE",
                            "skills",
                            f"source1={sorted(a)} source2={sorted(b)}",
                            "union of both taken; person_skill records which source supplied each",
                            severity="medium",
                        ),
                    )
                )

    # ---------------- audit trail + issue log --------------------------------
    for filename, pr in parsed_rows:
        pid = rid_to_person.get(pr.record.rid) if pr.record else None
        cur.execute(
            """INSERT OR IGNORE INTO source_record
                   (source_file, source_row, row_hash, raw_json, person_id, status, note)
               VALUES (?,?,?,?,?,?,?)""",
            (
                filename,
                pr.row_no,
                __import__("hashlib").sha1("\x1f".join(pr.raw).encode()).hexdigest(),
                raw_json(pr.raw),
                pid,
                pr.status,
                pr.note or None,
            ),
        )
        for iss in pr.issues:
            cur.execute(
                """INSERT INTO data_issue (source_file, source_row, issue_code, severity,
                        field, raw_value, action, resolved_value)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (filename, pr.row_no, iss.code, iss.severity, iss.field, iss.raw_value, iss.action, iss.resolved_value),
            )

    src_file_of = {"source1": "source1_naukri_applicants.csv", "source2": "source2_gig_workers.csv", "source3": "source3_cbnexus_contacts.csv"}
    for src, row, iss in merge_issues:
        cur.execute(
            """INSERT INTO data_issue (source_file, source_row, issue_code, severity,
                    field, raw_value, action, resolved_value)
               VALUES (?,?,?,?,?,?,?,?)""",
            (src_file_of[src], row, iss.code, iss.severity, iss.field, iss.raw_value, iss.action, iss.resolved_value),
        )

    for rv in result.reviews:
        cur.execute(
            "INSERT INTO match_review (reason, candidates) VALUES (?,?)",
            (rv["reason"], json.dumps(rv, sort_keys=True)),
        )
        cur.execute(
            """INSERT INTO data_issue (source_file, source_row, issue_code, severity,
                    field, raw_value, action)
               VALUES (?,?,?,?,?,?,?)""",
            (
                src_file_of[rv["candidates"][0]["source"]],
                rv["candidates"][0]["row"],
                "AMBIGUOUS_IDENTITY",
                "high",
                "name+city",
                " vs ".join(f"{c['source']}:{c['row']} {c['name']} ({c['city']})" for c in rv["candidates"]),
                "NOT merged automatically; queued in match_review for a human",
            ),
        )

    conn.commit()
    stats = summarise(conn)
    if verbose:
        print_report(stats, result)
    conn.close()
    return stats


def summarise(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "rows_read": q("SELECT COUNT(*) FROM source_record"),
        "rows_ingested": q("SELECT COUNT(*) FROM source_record WHERE status='ingested'"),
        "rows_skipped": q("SELECT COUNT(*) FROM source_record WHERE status IN ('skipped','rejected','duplicate_row')"),
        "persons": q("SELECT COUNT(*) FROM person"),
        "in_all_three": q("SELECT COUNT(*) FROM person WHERE in_source1+in_source2+in_source3=3"),
        "in_two": q("SELECT COUNT(*) FROM person WHERE in_source1+in_source2+in_source3=2"),
        "single_source": q("SELECT COUNT(*) FROM person WHERE in_source1+in_source2+in_source3=1"),
        "issues": q("SELECT COUNT(*) FROM data_issue"),
        "issue_types": q("SELECT COUNT(DISTINCT issue_code) FROM data_issue"),
        "reviews": q("SELECT COUNT(*) FROM match_review"),
        "by_code": dict(conn.execute("SELECT issue_code, COUNT(*) FROM data_issue GROUP BY 1 ORDER BY 2 DESC").fetchall()),
        "by_method": dict(conn.execute("SELECT match_methods, COUNT(*) FROM person GROUP BY 1 ORDER BY 2 DESC").fetchall()),
    }


def print_report(stats: dict, result) -> None:
    print("\n" + "=" * 70)
    print("CONSULTBAE MERGE PIPELINE")
    print("=" * 70)
    print(f"raw rows read      : {stats['rows_read']}")
    print(f"  ingested         : {stats['rows_ingested']}")
    print(f"  skipped/rejected : {stats['rows_skipped']}")
    print(f"unique people      : {stats['persons']}")
    print(f"  in all 3 sources : {stats['in_all_three']}")
    print(f"  in exactly 2     : {stats['in_two']}")
    print(f"  single source    : {stats['single_source']}")
    print(f"merge methods      : {stats['by_method']}")
    print(f"data issues logged : {stats['issues']} across {stats['issue_types']} distinct codes")
    print(f"queued for review  : {stats['reviews']}")
    print("\ntop issue codes:")
    for code, n in list(stats["by_code"].items())[:15]:
        print(f"  {n:>4}  {code}")
    if result.reviews:
        print("\nambiguous matches NOT auto-merged:")
        for rv in result.reviews:
            print(f"  - {rv['reason']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Merge the 3 ConsultBae sources into one SQLite database")
    ap.add_argument("--db", default=None, help="output database path")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    build(Path(args.db) if args.db else None, verbose=not args.quiet)
