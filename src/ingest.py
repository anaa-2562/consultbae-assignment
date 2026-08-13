"""Readers for the three raw CSVs.

Each reader is responsible for *structural* repair (bad row widths, repeated
headers, shifted columns, blank rows) and for handing back a normalised
``Record`` plus the list of issues found in that row.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .matching import Record
from .normalize import (
    EMAIL_RE,
    Issue,
    clean_text,
    normalize_bool,
    normalize_city,
    normalize_ctc,
    normalize_date,
    normalize_email,
    normalize_float,
    normalize_int,
    normalize_name,
    normalize_phone,
    normalize_rate,
    normalize_skills,
    normalize_status,
)


@dataclass
class ParsedRow:
    record: Record | None
    issues: list[Issue]
    raw: list[str]
    row_no: int
    status: str = "ingested"      # ingested | rejected | duplicate_row | skipped
    note: str = ""


def _hash(row: list[str]) -> str:
    return hashlib.sha1("\x1f".join(row).encode()).hexdigest()


def _read_raw(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for row_no, row in enumerate(reader, start=2):
            yield row_no, row, header


def _structural_checks(row: list[str], header: list[str], row_no: int) -> tuple[str | None, list[Issue]]:
    """Return (skip_status, issues). skip_status is None when the row is usable."""
    issues: list[Issue] = []
    if not any(c.strip() for c in row):
        return "skipped", [Issue("BLANK_ROW", "*", "", "row skipped entirely", severity="medium")]
    if [c.strip().lower() for c in row] == [c.strip().lower() for c in header]:
        return "skipped", [Issue("REPEATED_HEADER", "*", ",".join(row), "header row repeated mid-file, skipped", severity="high")]
    if len(row) != len(header):
        issues.append(
            Issue(
                "COLUMN_COUNT_MISMATCH",
                "*",
                f"{len(row)} cols vs {len(header)}",
                "row padded/truncated to header width",
                severity="high",
            )
        )
    return None, issues


def _repair_column_shift(row: list[str], email_idx: int) -> tuple[list[str], bool]:
    """source2 has one row whose columns are rotated (skill_tags shifted to the
    front). Detect it by locating the cell that actually looks like an email and
    rotating the row back so that cell sits in the email column."""
    if EMAIL_RE.match(clean_text(row[email_idx])):
        return row, False
    for i, cell in enumerate(row):
        if EMAIL_RE.match(clean_text(cell)):
            shift = (i - email_idx) % len(row)
            return row[shift:] + row[:shift], True
    return row, False


# --------------------------------------------------------------------------
def read_source1(path: Path):
    """Naukri applicants: Full Name, Email, Phone, City, Experience, CTC, Applied Date, Skills."""
    seen: dict[str, int] = {}
    for row_no, row, header in _read_raw(path):
        skip, issues = _structural_checks(row, header, row_no)
        if skip:
            yield ParsedRow(None, issues, row, row_no, status=skip)
            continue
        row = (row + [""] * len(header))[: len(header)]

        h = _hash([c.strip() for c in row])
        if h in seen:
            yield ParsedRow(
                None,
                issues + [Issue("EXACT_DUPLICATE_ROW", "*", f"identical to row {seen[h]}", "second copy dropped", severity="high")],
                row,
                row_no,
                status="duplicate_row",
                note=f"identical to row {seen[h]}",
            )
            continue
        seen[h] = row_no

        name = normalize_name(row[0])
        email = normalize_email(row[1])
        phone = normalize_phone(row[2])
        city = normalize_city(row[3])
        exp = normalize_float(row[4], "experience_years")
        ctc = normalize_ctc(row[5])
        applied = normalize_date(row[6])
        skills = normalize_skills(row[7])

        issues += name.issues + email.issues + phone.issues + city.issues
        issues += exp.issues + ctc.issues + applied.issues + skills.issues

        if exp.value is not None and not 0 <= exp.value <= 50:
            issues.append(Issue("EXPERIENCE_OUT_OF_RANGE", "experience_years", row[4], "kept, flagged", str(exp.value), "high"))

        rec = Record(
            rid=f"s1:{row_no}",
            source="source1",
            row=row_no,
            name=name.value,
            email=email.value,
            phone=phone.value,
            city=city.value,
            payload={
                "experience_years": exp.value,
                "current_ctc_inr": ctc.value[0],
                "ctc_source_unit": ctc.value[1],
                "applied_date": applied.value,
                "skills": skills.value,
                "raw_name": clean_text(row[0]),
            },
        )
        yield ParsedRow(rec, issues, row, row_no)


def read_source2(path: Path):
    """Gig workers: email_id, worker_name, rate, location, status, skill_tags."""
    seen: dict[str, int] = {}
    for row_no, row, header in _read_raw(path):
        skip, issues = _structural_checks(row, header, row_no)
        if skip:
            yield ParsedRow(None, issues, row, row_no, status=skip)
            continue
        row = (row + [""] * len(header))[: len(header)]

        repaired, was_shifted = _repair_column_shift(row, 0)
        if was_shifted:
            issues.append(
                Issue(
                    "COLUMN_SHIFT",
                    "*",
                    ",".join(row),
                    "columns rotated back into header order using the cell that parses as an email",
                    ",".join(repaired),
                    "high",
                )
            )
            row = repaired

        h = _hash([c.strip().lower() for c in row])
        if h in seen:
            yield ParsedRow(
                None,
                issues + [Issue("EXACT_DUPLICATE_ROW", "*", f"identical to row {seen[h]} after repair", "second copy dropped", severity="high")],
                row,
                row_no,
                status="duplicate_row",
                note=f"identical to row {seen[h]} after column repair",
            )
            continue
        seen[h] = row_no

        email = normalize_email(row[0])
        name = normalize_name(row[1])
        rate = normalize_rate(row[2])
        city = normalize_city(row[3])
        status = normalize_status(row[4])
        skills = normalize_skills(row[5])

        issues += email.issues + name.issues + rate.issues + city.issues + status.issues + skills.issues

        if not name.value:
            yield ParsedRow(None, issues, row, row_no, status="rejected", note="no worker_name")
            continue

        rec = Record(
            rid=f"s2:{row_no}",
            source="source2",
            row=row_no,
            name=name.value,
            email=email.value,
            phone=None,
            city=city.value,
            payload={
                "gig_rate_inr_hour": rate.value[0],
                "gig_rate_inr_month": rate.value[1],
                "gig_rate_unit": rate.value[2],
                "gig_status": status.value,
                "skills": skills.value,
                "raw_name": clean_text(row[1]),
            },
        )
        yield ParsedRow(rec, issues, row, row_no)


def read_source3(path: Path):
    """CBNexus contacts: Name, Phone Number, City, Verified, Projects Completed."""
    seen: dict[str, int] = {}
    for row_no, row, header in _read_raw(path):
        skip, issues = _structural_checks(row, header, row_no)
        if skip:
            yield ParsedRow(None, issues, row, row_no, status=skip)
            continue
        row = (row + [""] * len(header))[: len(header)]

        h = _hash([c.strip().lower() for c in row])
        if h in seen:
            yield ParsedRow(
                None,
                issues + [Issue("EXACT_DUPLICATE_ROW", "*", f"identical to row {seen[h]}", "second copy dropped", severity="high")],
                row,
                row_no,
                status="duplicate_row",
            )
            continue
        seen[h] = row_no

        name = normalize_name(row[0])
        phone = normalize_phone(row[1])
        city = normalize_city(row[2])
        verified = normalize_bool(row[3], "cbnexus_verified")
        projects = normalize_int(row[4], "projects_completed")

        issues += name.issues + phone.issues + city.issues + verified.issues + projects.issues

        if phone.value is None:
            issues.append(
                Issue(
                    "NO_JOIN_KEY",
                    "phone",
                    row[1],
                    "source3 has no email; without a phone this row can only match on name+city",
                    severity="high",
                )
            )

        rec = Record(
            rid=f"s3:{row_no}",
            source="source3",
            row=row_no,
            name=name.value,
            email=None,
            phone=phone.value,
            city=city.value,
            payload={
                "cbnexus_verified": verified.value,
                "projects_completed": projects.value,
                "raw_name": clean_text(row[0]),
            },
        )
        yield ParsedRow(rec, issues, row, row_no)


READERS = {
    "source1_naukri_applicants.csv": read_source1,
    "source2_gig_workers.csv": read_source2,
    "source3_cbnexus_contacts.csv": read_source3,
}


def raw_json(row: list[str]) -> str:
    return json.dumps(row, ensure_ascii=False)
