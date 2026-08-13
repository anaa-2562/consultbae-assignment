"""Throwaway-but-committed profiler: dumps every anomaly I could think of, with
evidence, so DATA_ISSUES.md is backed by output rather than eyeballing.

Run:  python scripts/profile_sources.py
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
FILES = {
    "source1": RAW / "source1_naukri_applicants.csv",
    "source2": RAW / "source2_gig_workers.csv",
    "source3": RAW / "source3_cbnexus_contacts.csv",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def rows(path: Path):
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        for i, row in enumerate(reader, start=2):  # line numbers as in the file
            yield i, row, header


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def phone_key(s: str) -> str:
    d = digits(s)
    return d[-10:] if len(d) >= 10 else d


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


for name, path in FILES.items():
    section(f"{name}: {path.name}")
    hdr = None
    widths = Counter()
    blank_rows, header_repeats, bad_email, short_phone = [], [], [], []
    n = 0
    for ln, row, header in rows(path):
        hdr = header
        n += 1
        widths[len(row)] += 1
        if not any(c.strip() for c in row):
            blank_rows.append(ln)
            continue
        if [c.strip().lower() for c in row] == [c.strip().lower() for c in header]:
            header_repeats.append(ln)
    print(f"header      : {hdr}")
    print(f"data rows   : {n}")
    print(f"row widths  : {dict(widths)}")
    print(f"blank rows  : {blank_rows}")
    print(f"header repeats mid-file: {header_repeats}")

# --- per-source deep dives -------------------------------------------------
section("source1 field-level anomalies")
cities, dates, ctc, phones = Counter(), [], [], Counter()
by_email, by_phone, by_name = defaultdict(list), defaultdict(list), defaultdict(list)
for ln, r, h in rows(FILES["source1"]):
    if not any(c.strip() for c in r):
        continue
    name, email, phone, city, exp, c, applied, skills = r
    cities[city] += 1
    dates.append((ln, applied))
    ctc.append((ln, name, c))
    phones[phone[:3]] += 1
    by_email[email.strip().lower()].append((ln, name))
    by_phone[phone_key(phone)].append((ln, name, email))
    by_name[name.strip().lower()].append((ln, email, phone))
    if not EMAIL_RE.match(email.strip()):
        print(f"  L{ln} malformed email: {email!r}")
print(f"  city variants ({len(cities)}): {dict(cities)}")
print(f"  phone prefixes: {dict(phones)}")
date_shapes = Counter()
for ln, d in dates:
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        date_shapes["YYYY-MM-DD"] += 1
    elif re.match(r"^\d{2}-\d{2}-\d{4}$", d):
        date_shapes["DD-MM-YYYY"] += 1
    elif re.match(r"^\d{2}/\d{2}/\d{4}$", d):
        date_shapes["MM/DD/YYYY?"] += 1
    else:
        date_shapes[f"text ({d})"] += 1
print(f"  date shapes: {dict(date_shapes)}")
lakhs = [(ln, nm, v) for ln, nm, v in ctc if float(v) < 1000]
absolute = [(ln, nm, v) for ln, nm, v in ctc if float(v) >= 1000]
print(f"  CTC looks-like-lakhs: {len(lakhs)} rows e.g. {lakhs[:3]}")
print(f"  CTC looks-like-rupees: {len(absolute)} rows e.g. {absolute[:3]}")
print("  duplicate emails:")
for k, v in by_email.items():
    if len(v) > 1:
        print(f"    {k}: {v}")
print("  duplicate phones (same person, maybe different email/name):")
for k, v in by_phone.items():
    if len(v) > 1:
        print(f"    {k}: {v}")
print("  repeated names:")
for k, v in by_name.items():
    if len(v) > 1:
        print(f"    {k}: {v}")

section("source2 field-level anomalies")
rates, statuses, locs = [], Counter(), Counter()
s2_emails = defaultdict(list)
for ln, r, h in rows(FILES["source2"]):
    if not any(c.strip() for c in r):
        continue
    email, wname, rate, loc, status, tags = r
    if not EMAIL_RE.match(email.strip()):
        print(f"  L{ln} column-shift / bad email -> row={r}")
        continue
    rates.append((ln, rate))
    statuses[status] += 1
    locs[loc] += 1
    s2_emails[email.strip().lower()].append((ln, wname))
print(f"  status variants: {dict(statuses)}")
print(f"  location variants ({len(locs)}): {dict(locs)}")
print(f"  rate units: {dict(Counter('per_hour' if '/hr' in r else 'per_month' if 'month' in r else '?' for _, r in rates))}")
for k, v in s2_emails.items():
    if len(v) > 1:
        print(f"  duplicate email: {k}: {v}")

section("source3 field-level anomalies")
ver, ph_shapes, s3_phone, s3_name = Counter(), Counter(), defaultdict(list), defaultdict(list)
for ln, r, h in rows(FILES["source3"]):
    if not any(c.strip() for c in r) or r[0].strip().lower() == "name":
        continue
    nm, ph, city, verified, projects = r
    ver[verified] += 1
    ph_shapes[
        "+91-" if ph.startswith("+91-") else "91" if ph.startswith("91") else "bare10"
    ] += 1
    s3_phone[phone_key(ph)].append((ln, nm))
    s3_name[nm.strip().lower()].append((ln, ph))
print(f"  verified variants: {dict(ver)}")
print(f"  phone shapes: {dict(ph_shapes)}")
for k, v in s3_phone.items():
    if len(v) > 1:
        print(f"  duplicate phone: {k}: {v}")
for k, v in s3_name.items():
    if len(v) > 1:
        print(f"  same name, different phone (likely DIFFERENT people): {k}: {v}")

# --- cross-source overlap --------------------------------------------------
section("cross-source linkage")
s1_email = {}
s1_phone = {}
for ln, r, h in rows(FILES["source1"]):
    if not any(c.strip() for c in r):
        continue
    s1_email[r[1].strip().lower()] = r[0]
    s1_phone[phone_key(r[2])] = r[0]
s2_email_set = {}
for ln, r, h in rows(FILES["source2"]):
    if not any(c.strip() for c in r) or not EMAIL_RE.match(r[0].strip()):
        continue
    s2_email_set[r[0].strip().lower()] = r[1]
s3_phone_set = {}
for ln, r, h in rows(FILES["source3"]):
    if not any(c.strip() for c in r) or r[0].strip().lower() == "name":
        continue
    s3_phone_set[phone_key(r[1])] = r[0]

print(f"  s1 unique emails: {len(s1_email)}  s2 unique emails: {len(s2_email_set)}")
print(f"  s1<->s2 email overlap: {len(set(s1_email) & set(s2_email_set))}")
print(f"  s1<->s3 phone overlap: {len(set(s1_phone) & set(s3_phone_set))}")
print(f"  s2-only emails: {len(set(s2_email_set) - set(s1_email))}")
print(f"  s3-only phones: {len(set(s3_phone_set) - set(s1_phone))}")
print("  NOTE: s2 has no phone, s3 has no email -> s2<->s3 can only link via s1 or name+city.")
s2_names = {v.strip().lower(): k for k, v in s2_email_set.items()}
s3_names = {v.strip().lower(): k for k, v in s3_phone_set.items()}
print(f"  s2<->s3 name-only overlap (NOT auto-merged): {sorted(set(s2_names) & set(s3_names))}")
