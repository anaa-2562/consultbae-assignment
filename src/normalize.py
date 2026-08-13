"""Field-level normalisers.

Every function here is pure and returns ``(value, issues)`` where ``issues`` is a
list of ``Issue`` records. Nothing is silently "fixed": if a value had to be
interpreted, coerced or rejected, it lands in the issue log and ends up in
``data_issues`` in SQLite (and from there in docs/DATA_ISSUES.md).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# Anything after this is not a plausible "applied on" date for this dataset.
# Pinned (not `date.today()`) so the pipeline is deterministic and re-runnable.
DATASET_AS_OF = date(2026, 8, 12)

MONTHLY_HOURS = 160  # 8h x 20 working days - documented assumption for rate conversion


@dataclass
class Issue:
    """One data-quality finding, tied to the exact cell it came from."""

    code: str
    field: str
    raw_value: str
    action: str
    resolved_value: str | None = None
    severity: str = "medium"  # low | medium | high


@dataclass
class Field:
    value: Any
    issues: list[Issue] = field(default_factory=list)


# --------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------
def clean_text(s: str | None) -> str:
    """Collapse whitespace (incl. the trailing spaces planted in city fields)."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------
_TITLES = {"mr", "mrs", "ms", "dr", "shri", "smt"}


def normalize_name(raw: str) -> Field:
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field("", [Issue("MISSING_NAME", "name", raw or "", "row rejected", severity="high")])

    if s.isupper() or s.islower():
        issues.append(
            Issue("NAME_CASING", "name", s, "title-cased for display; matching uses casefold")
        )

    parts = [p for p in re.split(r"\s+", s) if p.strip(".").lower() not in _TITLES]
    canonical = " ".join(p.capitalize() if not re.match(r"^[A-Z]\.$", p) else p.upper() for p in parts)

    if any(re.fullmatch(r"[A-Za-z]\.?", p) for p in parts):
        issues.append(
            Issue(
                "NAME_ABBREVIATED",
                "name",
                s,
                "initial kept; match key falls back to surname + first-initial",
                canonical,
            )
        )
    return Field(canonical, issues)


def name_key(raw: str) -> str:
    """Casefolded, punctuation-free key used for blocking."""
    return re.sub(r"[^a-z ]", "", clean_text(raw).lower()).strip()


def name_parts(raw: str) -> tuple[str, str]:
    """(first_token, surname). Surname is the last token - the stable half here."""
    toks = name_key(raw).split()
    if not toks:
        return "", ""
    if len(toks) == 1:
        return toks[0], toks[0]
    return toks[0], toks[-1]


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_email(raw: str) -> Field:
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_EMAIL", "email", raw or "", "left null; match on phone/name", severity="low")])
    if s != s.lower():
        issues.append(Issue("EMAIL_CASING", "email", s, "lower-cased (local part treated case-insensitively)", s.lower()))
    s = s.lower()
    if not EMAIL_RE.match(s):
        return Field(
            None,
            issues + [Issue("EMAIL_MALFORMED", "email", raw, "rejected, value quarantined in source_records", severity="high")],
        )
    return Field(s, issues)


def email_key(email: str | None) -> str | None:
    """Match key. Deliberately does NOT strip +tags or dots: `alt.nikhil.chopra70@`
    and `nikhil.chopra70@` are different mailboxes and are linked by phone instead."""
    return email.strip().lower() if email else None


# --------------------------------------------------------------------------
# phone
# --------------------------------------------------------------------------
def normalize_phone(raw: str) -> Field:
    """Return E.164 (+91XXXXXXXXXX) for Indian mobiles."""
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_PHONE", "phone", raw or "", "left null", severity="low")])

    digits = re.sub(r"\D", "", s)
    original_digits = digits
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    elif len(digits) == 13 and digits.startswith("091"):
        digits = digits[3:]

    if len(digits) != 10:
        return Field(
            None,
            [Issue("PHONE_UNPARSEABLE", "phone", raw, "left null, row still ingested", severity="high")],
        )

    if s != f"+91{digits}":
        issues.append(
            Issue(
                "PHONE_FORMAT",
                "phone",
                raw,
                "normalised to E.164 (+91) by stripping 0/91/+91-/spaces",
                f"+91{digits}",
                severity="low",
            )
        )
    if not re.match(r"^[6-9]", digits):
        issues.append(
            Issue("PHONE_IMPLAUSIBLE", "phone", raw, "kept, flagged: Indian mobiles start 6-9", f"+91{digits}")
        )
    return Field(f"+91{digits}", issues)


def phone_key(phone: str | None) -> str | None:
    """Last 10 digits - the only reliable cross-source join key for source3."""
    if not phone:
        return None
    d = re.sub(r"\D", "", phone)
    return d[-10:] if len(d) >= 10 else None


# --------------------------------------------------------------------------
# city
# --------------------------------------------------------------------------
CITY_CANON = {
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "blr": "Bengaluru",
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "delhi ncr": "Delhi",
    "ncr": "Delhi",
    "noida": "Noida",
    "pune": "Pune",
}


def normalize_city(raw: str) -> Field:
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_CITY", "city", raw or "", "left null", severity="low")])
    if s != raw:
        issues.append(Issue("CITY_WHITESPACE", "city", repr(raw), "trimmed", s, severity="low"))
    key = s.lower()
    canon = CITY_CANON.get(key)
    if canon is None:
        return Field(
            s.title(),
            issues + [Issue("CITY_UNKNOWN", "city", raw, "title-cased, not mapped to canonical list", s.title())],
        )
    if canon != s:
        issues.append(
            Issue(
                "CITY_VARIANT",
                "city",
                raw,
                f"mapped to canonical '{canon}' (casing/alias/NCR collapse)",
                canon,
                severity="low",
            )
        )
    return Field(canon, issues)


# --------------------------------------------------------------------------
# dates
# --------------------------------------------------------------------------
_TEXT_DATE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,})\s+(\d{4})$")


def normalize_date(raw: str) -> Field:
    """source1 mixes 5 date shapes.

    Evidence for reading slash dates as MM/DD/YYYY: rows like ``07/13/2026`` and
    ``08/19/2026`` have a >12 second component, which is only valid if the FIRST
    component is the month. Dash dates prove the opposite (``21-08-2026``), so
    ``DD-MM-YYYY`` is used there. Genuinely ambiguous values (both parts <= 12)
    are parsed with the format its siblings proved and flagged.
    """
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_DATE", "applied_date", raw or "", "left null", severity="low")])

    parsed: date | None = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        parsed = datetime.strptime(s, "%Y-%m-%d").date()
    elif re.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}", s):
        d, m, y = (int(x) for x in s.split("-"))
        if m > 12:
            return Field(None, [Issue("DATE_UNPARSEABLE", "applied_date", raw, "left null", severity="high")])
        parsed = date(y, m, d)
        issues.append(Issue("DATE_FORMAT_DDMMYYYY", "applied_date", raw, "parsed as DD-MM-YYYY", parsed.isoformat(), "low"))
        if d <= 12:
            issues.append(
                Issue("DATE_AMBIGUOUS", "applied_date", raw, "day and month both <=12; DD-MM assumed from sibling rows", parsed.isoformat())
            )
    elif re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", s):
        m, d, y = (int(x) for x in s.split("/"))
        if m > 12:
            m, d = d, m
        parsed = date(y, m, d)
        issues.append(Issue("DATE_FORMAT_MMDDYYYY", "applied_date", raw, "parsed as MM/DD/YYYY", parsed.isoformat(), "low"))
        if d <= 12:
            issues.append(
                Issue("DATE_AMBIGUOUS", "applied_date", raw, "day and month both <=12; MM/DD assumed from sibling rows", parsed.isoformat())
            )
    elif (m_ := _TEXT_DATE.match(s)):
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
        issues.append(Issue("DATE_FORMAT_TEXT", "applied_date", raw, "parsed textual date", parsed.isoformat() if parsed else None, "low"))

    if parsed is None:
        return Field(None, [Issue("DATE_UNPARSEABLE", "applied_date", raw, "left null", severity="high")])
    if parsed > DATASET_AS_OF:
        issues.append(
            Issue(
                "DATE_IN_FUTURE",
                "applied_date",
                raw,
                f"kept but flagged: applied date is after dataset as-of {DATASET_AS_OF}",
                parsed.isoformat(),
                "high",
            )
        )
    return Field(parsed.isoformat(), issues)


# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------
def normalize_ctc(raw: str) -> Field:
    """source1 CTC is two units in one column.

    Values < 1000 are lakhs-per-annum (2.4 - 11.9); values >= 1000 are absolute
    annual rupees (327,287 - 1,195,422). Multiplying the first group by 1e5 puts
    both on the same 3.2L - 11.9L distribution, which is the evidence for the
    split rather than a guess.
    """
    issues: list[Issue] = []
    s = clean_text(raw).replace(",", "")
    if not s:
        return Field((None, None), [Issue("MISSING_CTC", "current_ctc", raw or "", "left null", severity="low")])
    try:
        v = float(s)
    except ValueError:
        return Field((None, None), [Issue("CTC_UNPARSEABLE", "current_ctc", raw, "left null", severity="high")])

    if v <= 0:
        return Field((None, None), [Issue("CTC_NON_POSITIVE", "current_ctc", raw, "left null", severity="high")])

    if v < 1000:
        annual = int(round(v * 100_000))
        unit = "lpa"
        issues.append(
            Issue("CTC_UNIT_LAKHS", "current_ctc", raw, "interpreted as LPA, x100000 -> annual INR", str(annual), "low")
        )
    else:
        annual = int(round(v))
        unit = "inr_annual"

    if not 100_000 <= annual <= 10_000_000:
        issues.append(Issue("CTC_OUT_OF_RANGE", "current_ctc", raw, "kept, flagged as outside 1L-1Cr", str(annual)))
    return Field((annual, unit), issues)


_RATE_RE = re.compile(r"^([\d.]+)\s*(k?)\s*/\s*(hr|hour|month|mo)$", re.I)


def normalize_rate(raw: str) -> Field:
    """source2 rate is per-hour for some rows, per-month for others.

    Both are stored; the derived monthly figure uses MONTHLY_HOURS=160. The two
    populations do NOT reconcile (hourly x160 is ~3x the monthly rows), so the
    conflict is flagged instead of being averaged away.
    """
    issues: list[Issue] = []
    s = clean_text(raw).lower().replace(" ", "")
    if not s:
        return Field((None, None, None), [Issue("MISSING_RATE", "rate", raw or "", "left null", severity="low")])
    m = _RATE_RE.match(s)
    if not m:
        return Field((None, None, None), [Issue("RATE_UNPARSEABLE", "rate", raw, "left null", severity="high")])

    num, k, period = float(m.group(1)), m.group(2), m.group(3).lower()
    if k:
        num *= 1000
    if period in ("hr", "hour"):
        hourly, monthly, unit = num, num * MONTHLY_HOURS, "per_hour"
    else:
        hourly, monthly, unit = num / MONTHLY_HOURS, num, "per_month"
    issues.append(
        Issue(
            "RATE_MIXED_UNITS",
            "rate",
            raw,
            f"parsed as {unit}; counterpart derived with {MONTHLY_HOURS}h/month",
            f"{hourly:.2f}/hr | {monthly:.0f}/month",
            "low",
        )
    )
    if monthly > 150_000:
        issues.append(
            Issue(
                "RATE_SCALE_CONFLICT",
                "rate",
                raw,
                "flagged: hourly rows imply >1.5L/month, ~3x the explicit monthly rows - units are not reconcilable from the data alone",
                f"{monthly:.0f}/month",
                "high",
            )
        )
    return Field((round(hourly, 2), round(monthly), unit), issues)


# --------------------------------------------------------------------------
# enums / booleans
# --------------------------------------------------------------------------
STATUS_CANON = {"active": "active", "inactive": "inactive", "paused": "paused"}


def normalize_status(raw: str) -> Field:
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_STATUS", "gig_status", raw or "", "left null", severity="low")])
    canon = STATUS_CANON.get(s.lower())
    if canon is None:
        return Field(s.lower(), [Issue("STATUS_UNKNOWN", "gig_status", raw, "kept lower-cased, unmapped", s.lower())])
    if canon != s:
        issues.append(Issue("STATUS_CASING", "gig_status", raw, "case-folded to canonical enum", canon, "low"))
    return Field(canon, issues)


TRUE_TOKENS = {"y", "yes", "true", "1"}
FALSE_TOKENS = {"n", "no", "false", "0"}


def normalize_bool(raw: str, field_name: str = "verified") -> Field:
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_BOOL", field_name, raw or "", "left null (unknown != false)", severity="low")])
    low = s.lower()
    if low in TRUE_TOKENS:
        val = True
    elif low in FALSE_TOKENS:
        val = False
    else:
        return Field(None, [Issue("BOOL_UNPARSEABLE", field_name, raw, "left null", severity="high")])
    if s not in ("Yes", "No"):
        issues.append(Issue("BOOL_VARIANT", field_name, raw, "mapped Y/y/yes/N/n/No -> boolean", str(val), "low"))
    return Field(val, issues)


def normalize_int(raw: str, field_name: str) -> Field:
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_INT", field_name, raw or "", "left null", severity="low")])
    try:
        return Field(int(float(s)), [])
    except ValueError:
        return Field(None, [Issue("INT_UNPARSEABLE", field_name, raw, "left null", severity="high")])


def normalize_float(raw: str, field_name: str) -> Field:
    s = clean_text(raw)
    if not s:
        return Field(None, [Issue("MISSING_FLOAT", field_name, raw or "", "left null", severity="low")])
    try:
        return Field(float(s), [])
    except ValueError:
        return Field(None, [Issue("FLOAT_UNPARSEABLE", field_name, raw, "left null", severity="high")])


# --------------------------------------------------------------------------
# skills
# --------------------------------------------------------------------------
SKILL_CANON = {
    "n8n": "n8n",
    "langchain": "LangChain",
    "rest apis": "REST APIs",
    "rest api": "REST APIs",
    "mongodb": "MongoDB",
    "sql": "SQL",
    "mysql": "MySQL",
    "docker": "Docker",
    "zapier": "Zapier",
    "javascript": "JavaScript",
    "react": "React",
    "python": "Python",
    "selenium": "Selenium",
    "web scraping": "Web Scraping",
    "fastapi": "FastAPI",
    "pandas": "Pandas",
}


def normalize_skills(raw: str) -> Field:
    """source1 ships Title Case skills, source2 ships lower case - same vocabulary.
    Both collapse onto one controlled list so a person's skills de-duplicate on merge."""
    issues: list[Issue] = []
    s = clean_text(raw)
    if not s:
        return Field([], [Issue("MISSING_SKILLS", "skills", raw or "", "empty list", severity="low")])
    out: list[str] = []
    for tok in s.split(","):
        t = clean_text(tok).lower()
        if not t:
            continue
        canon = SKILL_CANON.get(t)
        if canon is None:
            canon = clean_text(tok)
            issues.append(Issue("SKILL_UNKNOWN", "skills", tok, "kept verbatim, outside controlled vocabulary", canon, "low"))
        if canon not in out:
            out.append(canon)
    if len(out) != len([t for t in s.split(",") if t.strip()]):
        issues.append(Issue("SKILL_DUPLICATE", "skills", raw, "duplicate skill tokens removed", ", ".join(out), "low"))
    return Field(out, issues)


def merge_issue_lists(*fields: Field) -> list[Issue]:
    out: list[Issue] = []
    for f in fields:
        out.extend(f.issues)
    return out


def dedupe(seq: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
