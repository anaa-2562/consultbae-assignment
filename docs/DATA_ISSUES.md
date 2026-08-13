# Task 4 — Data issues report

Every issue below was found by profiling the three files, is reproduced by
`python scripts/profile_sources.py`, and is logged row-by-row into the
`data_issue` table when the pipeline runs. **308 individual findings across 24
distinct issue codes.** Counts in this document come from that table, not from
eyeballing the CSVs.

```bash
python -m src.pipeline                       # rebuild + print the summary
python scripts/generate_issue_report.py      # regenerate docs/DATA_ISSUES_APPENDIX.md
```

Severity here means: **high** = would corrupt the merged data or silently lose a
person if ignored; **medium** = would break joins or reporting; **low** =
cosmetic inconsistency that still has to be normalised for keys to work.

---

## A. Structural problems — the file isn't even a clean table

### A1. Repeated header row in the middle of the file — HIGH
`source3_cbnexus_contacts.csv` line 16 is a second copy of the header. Read naively
it becomes a "person" called *Name* with the phone number *Phone Number*.

**Done:** rows whose cells equal the header are detected and skipped
(`_structural_checks`, `ingest.py`), logged as `REPEATED_HEADER`. Detected by
comparison against the header, not by hard-coding line 16 — a second occurrence
would also be caught.

### A2. Completely blank row — MEDIUM
`source2_gig_workers.csv` line 12 is `,,,,,`. It parses as a valid 6-column row with
an empty name and email.

**Done:** skipped and logged as `BLANK_ROW`. Not counted as a person.

### A3. Shifted columns — one row's fields are rotated — HIGH
`source2_gig_workers.csv` line 20:

```
react, javascript, mysql | ISHA.CHOPRA95@... | Isha Chopra | 1406/hr | Pune | active
```

The skill list has moved into `email_id` and every other field is one column to the
right. Ingested as-is, this creates a worker whose email is *"react, javascript,
mysql"* and whose rate is the string *"Isha Chopra"*.

**Done:** the repair finds the cell that actually parses as an email address and
rotates the row so that cell lands back in the email column
(`_repair_column_shift`). It is generic — it fixes any rotation, not just this one.
After repair the row turns out to be a **byte-identical duplicate of line 7**, so it
is dropped as `EXACT_DUPLICATE_ROW`. Two bugs behind one symptom.

### A4. Duplicate rows — HIGH
Line 20 of source2 (above) after repair. Detected by hashing the normalised row, so
this is not specific to the one planted case.

---

## B. Identity problems — the actual point of the exercise

**There is no shared ID.** source1 has email + phone, source2 has email only,
source3 has phone only. So source2 and source3 have *no strong identifier in
common* and can only be linked transitively through source1, or by a weak signal.
The matcher runs three tiers into a union-find structure, so A=B and B=C ⇒ A=C:

| Tier | Rule | Confidence | Links made |
|---|---|---|---|
| 1 | normalised email equality | 1.00 | 16 |
| 2 | last-10-digit phone equality | 0.99 | 11 |
| 3 | fuzzy name + canonical city, only when unambiguous | 0.80 | 4 |

**105 raw rows → 102 ingested → 56 unique people.** 15 people appear in all three
systems, 14 in exactly two, 27 in one.

### B1. Same person, abbreviated name — HIGH
source1 line 25 `R. Verma` and line 31 `Rohit Verma` — same email, same phone. A
name-based matcher scores these ~50% and splits them.

**Done:** matched on email (tier 1), so the abbreviation never mattered. The display
name picks the most complete spelling (`Rohit Verma`); `R. Verma` is kept in
`person_name_alias`. `names_compatible()` also handles initials directly
(surname equal + first initial matches), for cases where no email exists.

### B2. Same person, two different email addresses — HIGH
source1 lines 27 and 37: `alt.nikhil.chopra70@example.com` and
`nikhil.chopra70@example.com`, **same phone number** `09000000103`.

**Done:** email matching alone would keep them apart; the phone tier merges them.
Both addresses stay in `person_email` so inbound mail on either resolves, and the
address that best matches the person's name is promoted to `primary_email` — the
`alt.` one is not, which matters because that column is what a recruiter mails.
Logged as `MULTIPLE_EMAILS_ONE_PERSON` + `ALTERNATE_EMAIL`.

Deliberately **not** done: stripping dots or `+tags` to canonicalise emails. Those
are different mailboxes at most providers; guessing there would merge real people.

### B3. Different people, same name, different city — HIGH
Two `Deepak Nair`s: `deepak.nair44@example.com` in Bengaluru (present in all three
files) and `deepak.nair57@example.in` in New Delhi (source2 only). A name-only
matcher collapses them into one worker with two rates and two cities.

**Done:** kept as two people. City is part of the tier-3 blocking key, and distinct
emails veto a weak merge.

### B4. Different people, same name, *same* city — HIGH (the nastiest one)
Three `Arjun Mehta` records in Noida:

| Where | Email | Phone |
|---|---|---|
| source1:20 | arjun.mehta9@example.in | 9000000131 |
| source3:5 | — | 9000000131 |
| source3:28 | — | 9000000272 |
| source2:18 | arjun.mehta77@mailtest.example.org | — |

source1:20 and source3:5 share a phone, so they are one person with certainty.
The other two share only a name and a city with that person — and with each other.

**Done:** three separate people. Tier 3 refuses to fire when any source contributes
more than one name-compatible record to a (name, city) block, and refuses any merge
that would put two distinct emails or two distinct phones on one person. The block
is written to the `match_review` table with all four candidates, and surfaces as
`AMBIGUOUS_IDENTITY`.

This is the decision I'd defend hardest: **a wrong merge is unrecoverable** (two
people's rates, CTCs and project counts are now one row), while a missed merge is a
5-second human fix from a review queue. So the tier-3 matcher is biased towards
refusing.

### B5. source2 ↔ source3 have no key in common — HIGH
source2 has emails but no phones; source3 has phones but no emails. 20 names occur
in both. Sixteen of those are already joined through source1. The remaining
**four — Manish Bhatia, Divya Chopra, Karan Chopra, Vikram Mehta —** exist *only*
in source2 and source3.

**Done:** matched on name + canonical city (tier 3) because each block was
unambiguous, recorded at confidence 0.80 with `match_methods='name_city'` so anyone
querying can filter them out. Skipping tier 3 entirely would leave those four people
duplicated with half a profile each; applying it blindly would have merged the Arjun
Mehtas. The confidence column is how both facts coexist.

### B6. Cross-source name casing — MEDIUM
source3 stores `RITU SHARMA`, `MEERA BHATIA`; source2 stores emails in caps
(`ISHA.CHOPRA95@MAILTEST.EXAMPLE.ORG`).

**Done:** casefold for matching, Title Case for display, originals preserved as
aliases (9 × `NAME_CASING`, 9 × `EMAIL_CASING`).

---

## C. Format problems — same fact, many spellings

### C1. Phone numbers in five shapes — LOW but blocking
`+919000000254`, `9000000237`, `09000000287`, `919000000231`, `+91-9000000131`.
Sixty rows needed normalising. Without this the phone tier matches almost nothing —
this single fix is what links source3 to source1 at all.

**Done:** strip non-digits, drop `91`/`0`/`091` prefixes, keep the last 10, store
E.164 (`+91XXXXXXXXXX`). `phone_key()` (last 10 digits) is the join key.

### C2. City names: 16 spellings for 5 cities — LOW but blocking
`GURGAON` / `gurugram ` / `Gurugram`, `Bengaluru` / `bangalore` / `Bangalore`,
`Delhi` / `New Delhi` / `new delhi` / `Delhi NCR`, `Noida ` with a trailing space.

**Done:** canonical map, `Gurgaon→Gurugram`, `Bangalore→Bengaluru`, and the whole
`Delhi / New Delhi / Delhi NCR` family collapsed to `Delhi`.

*Evidence the NCR collapse is right, not lazy:* after it, **zero** merged people have
conflicting cities across sources (`CROSS_SOURCE_CITY_CONFLICT` count = 0). The
same humans are described as `new delhi` in source1, `Delhi` in source2 and
`New Delhi` in source3. Gurugram and Noida are kept distinct from Delhi even though
they are technically NCR, because the files use them as separate cities.

### C3. Applied dates in five formats, two of them mutually contradictory — MEDIUM
`2026-08-08` (ISO), `24-07-2026` (DD-MM), `07/13/2026` (MM/DD), `7 Jul 2026` (text).

The dangerous part: `01-08-2026` and `07/03/2026` are valid under both DD-MM and
MM-DD, and the two families disagree about which comes first.

**Done:** the shape settles it. Dash dates include `21-08-2026` — day 21 cannot be a
month, so dashes are DD-MM-YYYY. Slash dates include `07/13/2026` and `08/19/2026` —
13 and 19 cannot be months, so slashes are MM/DD/YYYY. That rule is applied to the
ambiguous siblings within each family and **8 rows are flagged `DATE_AMBIGUOUS`**
anyway, because the inference is about the format, not about that specific row.

### C4. Applied dates in the future — HIGH
8 rows are dated after the 12 Aug 2026 assignment date (`21-08-2026`, `22-08-2026`,
`08/19/2026`, `08/21/2026`…). Someone cannot have applied next week.

**Done:** parsed and kept, flagged `DATE_IN_FUTURE`. Not silently dropped — if this
were a real feed I'd want to know whether the source system has a timezone or
data-entry bug before deleting anyone's application. The as-of date is a pinned
constant (`DATASET_AS_OF`), not `today()`, so the pipeline stays deterministic.

### C5. `Current CTC` holds two different units in one column — HIGH
21 rows look like `417964`, `775670`, `1195422`; the other 21 look like `4.2`,
`8.3`, `11.9`.

**Done:** values < 1000 are read as lakhs-per-annum and multiplied by 100,000. The
evidence is distributional, not a guess: the "absolute" rows span
₹3.27L–₹11.95L and the small rows span 2.4–11.9 LPA. After conversion both land
on the *same* ₹2.4L–₹11.9L distribution. Everything is stored as annual INR in
`current_ctc_inr`, with the original unit kept in `ctc_source_unit` so the guess is
auditable.

### C6. `rate` mixes ₹/hour and ₹/month — HIGH
`1415/hr`, `403/hr`, `15k/month`, `72k/month`, plus a `k` multiplier only on the
monthly ones. Averaging the raw column is meaningless.

**Done:** parsed into `gig_rate_inr_hour` **and** `gig_rate_inr_month`, converting
at a documented 160 h/month, and the original unit kept.

**But the two populations do not reconcile,** and I flagged that rather than hiding
it: `1415/hr × 160 = ₹2.26L/month`, roughly 3× the explicit monthly rows (₹15k–79k).
Either the hourly figures are not really per hour, or these are two different pay
tiers. 7 rows raise `RATE_SCALE_CONFLICT`. This is a question for whoever owns the
gig system — the honest output is a flag, not an averaged number that looks fine.

### C7. `status` in five casings — LOW
`Active`, `active`, `ACTIVE`, `Inactive`, `paused` → three canonical values.

### C8. `Verified` in five spellings — LOW
`Y`, `Yes`, `yes`, `N`, `No` → boolean. **Unknown/blank maps to NULL, not `false`** —
"we never checked" and "we checked and they failed" are different facts, and
collapsing them silently downgrades verified workers.

### C9. Skills: same vocabulary, two casings — LOW
source1 writes `n8n, LangChain, REST APIs`; source2 writes `n8n, langchain, rest apis`.
Left alone, one person's merged skill list contains every skill twice.

**Done:** a controlled vocabulary maps both onto one spelling, and skills are stored
in a `person_skill` link table that records *which source* supplied each — so the
merged view de-duplicates while provenance survives. Skills outside the vocabulary
would be kept verbatim and flagged `SKILL_UNKNOWN` (count: 0 for these files).

### C10. Trailing whitespace — LOW
`"Noida "`, `"gurugram "` — 14 rows. Invisible in a spreadsheet, fatal to a
`GROUP BY`.

---

## D. Things I checked that turned out to be clean

Worth stating, because "no issues found" is only meaningful if you looked:

- **Experience years** — all 42 rows in 0.8–5.6, no negatives, no nulls, no
  out-of-range values.
- **Projects completed** — all 0–15 integers; the `0` for Sahil Malhotra is a real
  zero, not a missing value.
- **Email syntax** — every email except the shifted-column cell parses.
- **Phone plausibility** — every number is 10 digits starting with 9.
- **Cross-source city conflicts after canonicalisation** — zero (see C2).
- **Skill-set divergence between source1 and source2 for the same person** — zero;
  where a person is in both files the two skill lists agree exactly, which is
  corroborating evidence that the email/phone matches are correct.

---

## E. Issue codes at a glance

| Code | Severity | Count | Where |
|---|---|---|---|
| `CITY_VARIANT` | low | 60 | all three |
| `PHONE_FORMAT` | low | 60 | source1, source3 |
| `RATE_MIXED_UNITS` | low | 30 | source2 |
| `STATUS_CASING` | low | 22 | source2 |
| `CTC_UNIT_LAKHS` | low | 21 | source1 |
| `BOOL_VARIANT` | low | 18 | source3 |
| `CITY_WHITESPACE` | low | 14 | all three |
| `DATE_FORMAT_DDMMYYYY` | low | 12 | source1 |
| `DATE_FORMAT_MMDDYYYY` | low | 11 | source1 |
| `DATE_FORMAT_TEXT` | low | 10 | source1 |
| `EMAIL_CASING` | medium | 9 | source2 |
| `NAME_CASING` | medium | 9 | source3 |
| `DATE_AMBIGUOUS` | medium | 8 | source1 |
| `DATE_IN_FUTURE` | **high** | 8 | source1 |
| `RATE_SCALE_CONFLICT` | **high** | 7 | source2 |
| `AMBIGUOUS_IDENTITY` | **high** | 1 | source1/2/3 |
| `COLUMN_SHIFT` | **high** | 1 | source2 |
| `EXACT_DUPLICATE_ROW` | **high** | 1 | source2 |
| `MULTIPLE_EMAILS_ONE_PERSON` | **high** | 1 | source1 |
| `REPEATED_HEADER` | **high** | 1 | source3 |
| `BLANK_ROW` | medium | 1 | source2 |
| `NAME_ABBREVIATED` | medium | 1 | source1 |
| `NAME_VARIANTS_MERGED` | low | 1 | source1 |
| `ALTERNATE_EMAIL` | low | 1 | source1 |

Query any of them back to the exact source cell:

```sql
SELECT source_file, source_row, raw_value, action, resolved_value
FROM data_issue WHERE issue_code = 'DATE_IN_FUTURE';
```

---

## F. What I did *not* fix, and why

| Left alone | Why |
|---|---|
| Future applied dates | Kept + flagged. Deleting applications on a hunch is worse than surfacing 8 flags. |
| Hourly vs monthly rate mismatch (C6) | Cannot be resolved from the data. Reconciling it would mean inventing an exchange rate between two pay models. |
| The 4 `name_city` merges | Applied, but stamped 0.80 confidence so downstream can exclude them. |
| The `Arjun Mehta` block | Queued for a human in `match_review`. A guess here is unrecoverable. |
| Email dot/`+tag` canonicalisation | Different mailboxes at most providers. Would merge distinct people. |
