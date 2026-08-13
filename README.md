# ConsultBae — AI Automation take-home

Three messy CSVs from three systems, merged into one clean database, with an n8n
LLM automation on top and a small audio-collection app writing back into the same
`person` table.

| Task | What | Where |
|---|---|---|
| 1 | Merge 3 sources → one SQLite DB, identity resolution | `src/`, `python -m src.pipeline` |
| 2 | n8n flow: LLM tags each person's skill category, writes back | `n8n/consultbae_skill_tagger.json` |
| 3 | Audio collection app + property extraction | `app/`, `src/audio_features.py` |
| 4 | Data issues report | [`docs/DATA_ISSUES.md`](docs/DATA_ISSUES.md) |
| 5 | 5,000 workers in a weekend — what breaks | [`docs/SCALING.md`](docs/SCALING.md) |

**Headline numbers:** 105 raw rows → 102 ingested → **56 unique people**
(15 in all three systems). **308 data-quality findings across 24 issue codes**, each
logged back to the exact source cell. 1 identity block deliberately left unmerged
and queued for a human. 57 tests.

---

## Setup

Requires Python 3.11+ and **ffmpeg** (used for audio decoding and probing).

```bash
git clone <this repo> && cd consultbae-assignment

python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

sudo apt install ffmpeg          # macOS: brew install ffmpeg
```

### 1. Build the merged database

```bash
python -m src.pipeline
```

Rebuilds `data/consultbae.db` from `data/raw/*.csv` and prints the merge summary.
It is a full rebuild and is deterministic — run it as many times as you like.

```
raw rows read      : 105
  ingested         : 102
  skipped/rejected : 3
unique people      : 56
  in all 3 sources : 15
  in exactly 2     : 14
  single source    : 27
merge methods      : {'single_source': 25, 'email,phone': 16, 'phone': 11, 'name_city': 4}
data issues logged : 308 across 24 distinct codes
queued for review  : 1

ambiguous matches NOT auto-merged:
  - ambiguous name+city block: 'Arjun Mehta' in Noida appears 1x in source1,
    1x in source2, 2x in source3 - name+city cannot pick the right one
```

### 2. Run the app

```bash
uvicorn app.main:app --reload --port 8000
```

- <http://localhost:8000/> — record in the browser or upload a file
- <http://localhost:8000/submissions> — all submissions, players, extracted properties
- <http://localhost:8000/docs> — API

Browser mic recording needs a secure context: `localhost` is fine, a bare LAN IP is
not (use `ngrok http 8000` to demo from a phone).

### 3. Run the n8n flow

Import `n8n/consultbae_skill_tagger.json`, point the **Config** node at your app,
add a chat-model credential, execute. Full steps and the reasoning behind the
design: [`n8n/README.md`](n8n/README.md).

### 4. Tests

```bash
python -m pytest -q          # 57 tests
python scripts/profile_sources.py          # raw-data profiler (the evidence behind Task 4)
python scripts/generate_issue_report.py    # regenerate the issues appendix from the DB
```

---

## How the merge works

No shared ID exists across the three files:

```
source1 (naukri)   name, EMAIL, PHONE, city, experience, CTC, applied date, skills
source2 (gig)      name, EMAIL,   —  , city, rate, status, skill tags
source3 (cbnexus)  name,   —  , PHONE, city, verified, projects completed
```

source2 and source3 share **no** strong identifier, so they can only be linked
through source1 or by a weak signal. Three tiers feed a union-find structure, so
A=B and B=C ⇒ A=C:

| Tier | Rule | Confidence | Links |
|---|---|---|---|
| 1 | normalised email equality | 1.00 | 16 |
| 2 | last-10-digit phone equality | 0.99 | 11 |
| 3 | fuzzy name + canonical city, **only when unambiguous** | 0.80 | 4 |

Tier 3 refuses to fire — and writes to `match_review` instead — when any source
contributes more than one name-compatible record to a (name, city) block, or when
the merge would put two different emails or two different phones on one person.
That is what keeps the three `Arjun Mehta`s in Noida apart while still linking the
four people who exist *only* in source2 and source3.

`match_methods` and `match_confidence` are stored per person, so anyone querying can
exclude the weak matches.

### Schema

```
person                golden record, one row per human
  person_email        alternate mailboxes (same person, two emails)
  person_phone        alternate numbers
  person_name_alias   every spelling seen ("R. Verma", "RITU SHARMA")
  person_skill        → skill, with the source that supplied it
source_record         every raw row verbatim as JSON + what happened to it
data_issue            every quality finding, tied to source file + row
match_review          matches the pipeline refused to make automatically
audio_submission      Task 3 clips + extracted properties, FK → person
v_person_full         flattened view used by the app and the n8n flow
```

Every merged field can be traced back to the exact source cell it came from:

```sql
SELECT sr.source_file, sr.source_row, sr.raw_json
FROM source_record sr WHERE sr.person_id = 24;
```

---

## Audio properties (Task 3)

Extracted for every submission and stored on `audio_submission`:

| Required | How |
|---|---|
| duration | ffprobe container duration, falling back to decoded sample count |
| sample rate (kHz) | ffprobe stream `sample_rate` |
| bitrate (kbps) | ffprobe stream/format bitrate, falling back to `bytes × 8 / duration` |
| loudness (dB) | RMS of the decoded signal in dBFS |

| Bonus | How |
|---|---|
| estimated SNR | frame the signal at 30 ms, take the 90th percentile frame RMS as signal and the 10th as noise floor, report the ratio in dB |
| clipping % | share of samples at ≥ 0.999 full scale |
| near-silence % | share of frames more than 30 dB below the loud percentile |
| spectral flatness | ~1 = noise-like hiss, ~0 = tonal/voice (librosa) |
| quality grade | explainable rule-based `good / fair / poor` + the reason in words |

Two deliberate calls:

- **Loudness is dBFS RMS, not LUFS.** Proper EBU R128 needs K-weighting and a gating
  algorithm; dBFS RMS is the honest approximation and is labelled as such rather than
  called "LUFS".
- **The quality grade is rules, not a model.** An ops person has to tell a worker
  *why* a clip was rejected. "Too quiet, −38 dBFS, and 74% silence" is actionable;
  `0.31` is not.

A submission is linked to an existing person by the same phone key the pipeline
uses, so a recording from an existing applicant attaches to their record instead of
creating a duplicate. An unknown number creates a new person tagged `audio_app`.

---

## Stuck log

> The three places this actually cost me time. Written up honestly, including the
> suggestions I threw away.

### 1. My matcher silently merged two different people, and the output looked fine

**What happened.** After the first working version of the merge I ran a spot-check on
names that appear more than once, rather than trusting the summary line. Person #19
had come out as `Arjun Mehta` in Noida with **two different email addresses** —
`arjun.mehta9@example.in` from source1 and `arjun.mehta77@mailtest.example.org` from
source2. My tier-3 (name + city) rule had joined them, and my "is this ambiguous?"
check had passed, because it only counted candidates in the *two* sources being
compared. source3 had two different `Arjun Mehta`s in Noida — one of them a
completely different human — and neither of them was part of that comparison.

The unit tests were green. Nothing crashed. That is what makes this the worst kind
of bug: 56 people, one of them quietly fictional.

**What I searched.** "entity resolution blocking key", "record linkage precision vs
recall", "union find transitive closure duplicate detection". The most useful thing I
read was on Fellegi–Sunter record linkage: split matches into *match / possible match
/ non-match* and route the middle band to clerical review, rather than forcing a
binary decision. That framing is what turned into my `match_review` table.

**What I asked AI, and what I rejected.** I asked for ways to make fuzzy name
matching more accurate, and got the expected menu: raise the rapidfuzz threshold, add
Soundex/Metaphone, weight the surname, try `token_set_ratio`. **I rejected all of
it**, because none of it addresses the actual failure. The two records didn't match
by accident on a fuzzy score — the names were *character-for-character identical*,
in the same city. No string metric can separate two real people who share a name and
a city. Tuning the threshold would have felt like progress while changing nothing.

**How I got unstuck.** I stopped trying to make the weak signal stronger and made it
*abstain* instead. Three vetoes, all in `_ambiguity_reason()`:

1. if any source contributes more than one name-compatible record to a (name, city)
   block, no tier-3 merge happens anywhere in that block;
2. a weak merge that would put two distinct emails on one person is rejected — if
   they were one person the email tier would already have linked them, so two emails
   is evidence *against* the match, not neutral;
3. same argument for two distinct phone numbers.

Everything vetoed goes to `match_review` with all candidates attached. The
asymmetry is the whole point: **a wrong merge is unrecoverable** — two people's CTC,
rate and project counts are now one row and you cannot tell which was which — while
a missed merge is a five-second fix from a queue. `test_ambiguous_same_name_same_city_is_not_merged`
locks the behaviour in, and `test_genuine_source2_to_source3_links_are_made` makes
sure I didn't fix it by disabling tier 3 altogether, which would have quietly cost me
four real merges.

### 2. Bitrate cannot be computed from audio samples, which took me a while to accept

**What happened.** I had duration, sample rate and loudness working from the decoded
signal within about twenty minutes, and then spent longer than I want to admit trying
to get bitrate the same way. I kept computing `sample_rate × bit_depth × channels`,
which gives 705.6 kbps for every WAV — correct for PCM, and completely wrong for the
WebM/Opus blob a browser actually uploads, where it should be ~32.

**Where I got stuck.** The conceptual bit: once a file is decoded into floats, the
compression is *gone*. Bitrate is a property of the encoded container, not of the
signal. No amount of numpy gets it back.

**What I searched.** "librosa get bitrate" (the answer is: it doesn't, by design),
"ffprobe show_streams bit_rate", "webm opus duration missing". Then a bug I hit
straight after: `ffprobe` on a Chrome-recorded WebM sometimes reports **no** bitrate
at all, because MediaRecorder writes a live-stream container without a duration
header.

**What I asked AI, and what I rejected.** The first suggestion was `mutagen`. It does
read bitrate — but it's another dependency that only covers some container formats,
and I already needed ffmpeg for decoding, so ffprobe was free. I also rejected
hardcoding a bitrate per codec (a plausible-looking lie in a database column is worse
than a null).

**How I got unstuck.** I split the extractor into two layers that answer different
questions: **ffprobe for container truth** (codec, channels, declared sample rate,
bitrate) and **decoded samples for signal truth** (RMS loudness, peak, clipping,
SNR). When ffprobe reports no bitrate, I compute the real average as
`file_size × 8 / duration` rather than leaving it null or inventing a constant.
`test_compressed_formats_decode` asserts a real Opus file reports 10–80 kbps and not
PCM's 700+, which is exactly the bug I'd shipped otherwise.

The same split solved a second problem: I was originally decoding via
`librosa.load()`, which handles WebM only through the deprecated `audioread` path and
emits a removal warning. Since ffmpeg was already a hard dependency, decoding through
`ffmpeg -f f32le pipe:1` was both faster and one fewer thing to break.

### 3. `TypeError: unhashable type: 'dict'` from inside Jinja's template cache

**What happened.** The API worked end to end — submissions, extraction, write-back,
all of it — but both HTML pages returned 500 with a traceback that ended deep inside
`jinja2/utils.py` at `rv = self._mapping[key]`. Nothing in the traceback mentioned my
code beyond the `TemplateResponse` call.

**Where I got stuck.** I spent the first few minutes in the wrong place entirely,
assuming a mangled template or a version clash — I checked for two jinja2 installs on
the path and found 3.1.6 in both. The templates were fine.

**What I searched.** The error text plus "starlette", which surfaced the actual
cause: newer Starlette changed `TemplateResponse` to `(request, name, context)`. The
old `(name, {"request": request})` signature is deprecated, and the new one
interprets my template *name* string as the request and my *context dict* as the
template name — which is why a dict ended up as a cache key.

**What I asked AI, and what I rejected.** The first suggestion was to pin an older
Starlette. I rejected it: pinning a dependency backwards to keep a deprecated call
site working is a debt you pay at the worst possible moment, and this was a two-line
fix in my code. I also rejected switching to plain `HTMLResponse` f-strings to make
the problem disappear — that trades a real fix for a worse codebase.

**How I got unstuck.** Read the signature change, moved both call sites to
`TemplateResponse(request, "index.html", {...})`, done. The lesson I actually took
away: when a traceback bottoms out in a library's internals with a type error, the
suspect is usually *how* I called the boundary, not what's inside it.

---

## Repo layout

```
src/
  normalize.py       field-level normalisers; every one returns (value, issues)
  matching.py        union-find + the three matching tiers + the ambiguity vetoes
  ingest.py          per-source readers, structural repair (shift/header/blank rows)
  pipeline.py        Task 1 entrypoint: ingest → resolve → write golden records
  schema.sql         the merged schema, with the design reasoning in comments
  audio_features.py  Task 3 extraction (ffprobe + signal analysis)
  db.py              connection helpers
app/
  main.py            FastAPI: pages, submission API, n8n endpoints
  templates/         record page + submissions list
n8n/
  consultbae_skill_tagger.json   the exported flow
  README.md                      import steps + design decisions
scripts/
  profile_sources.py           raw-data profiler (evidence for Task 4)
  generate_issue_report.py     regenerates the issues appendix from the DB
  verify_api_for_n8n.py        exercises the flow's endpoints without an LLM key
docs/
  DATA_ISSUES.md               Task 4 report
  DATA_ISSUES_APPENDIX.md      generated evidence
  SCALING.md                   Task 5
tests/                         57 tests: normalisers, the identity traps, audio
data/raw/                      the three source CSVs, untouched
```

## AI use

I used Claude while building this, mostly for API surface I hadn't used before
(MediaRecorder mime negotiation, ffprobe flags, n8n's node JSON shape) and as a
rubber duck on the matching design. Every decision that mattered — the three-tier
matcher, the abstain-and-queue behaviour, the ffprobe/signal split, the dBFS-not-LUFS
call — is mine and is argued for above or in the code comments. The stuck log records
the AI suggestions I rejected and why, because those were the more useful moments.
