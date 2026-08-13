# 6-minute video script

Timed to **~5:40** including the pauses where you're clicking and things are
loading. Hard ceiling is 6:00. The spoken lines below are ~800 words, which is
about 5 minutes 40 at a normal speaking pace.

Know the beats, then say them in your own words. Slightly rough and confident
beats polished and read out.

---

## Before you hit record (10 minutes of setup, saves 3 retakes)

**Terminal 1 — app running:**
```bash
cd consultbae-assignment && source .venv/bin/activate
uvicorn app.main:app --port 8000
```

**Terminal 2 — the one you type in on camera.** Pre-run everything once so nothing
downloads or compiles live, then `clear`.

**n8n already running, flow already imported, credential already added, and
executed successfully at least once.** You'll re-run it on camera. You do not want
to be debugging an API key on video.

**Browser tabs, left to right:**

1. `http://localhost:8000/` — submit page
2. `http://localhost:8000/submissions`
3. `http://localhost:5678` — n8n, flow open on the canvas
4. GitHub repo
5. `docs/DATA_ISSUES.md` rendered on GitHub

**Reset state so the demo is clean:**
```bash
rm -f data/consultbae.db* data/audio/2026*
python -m src.pipeline --quiet
```
Then submit one clip through the app so the submissions page isn't empty — you'll
add a second one live.

**Recording:** 1440×900 or 1080p, terminal font bumped up, mic close, notifications
off. Loom or OBS, both fine. Say your name once at the start. **Never say "the AI
wrote this"** — you built it, you're explaining it.

---

## The script

### 0:00 – 0:20 — Intro *(GitHub repo)*

> "Hi, I'm Dipanshu — my submission for the ConsultBae AI Automation assignment.
> Three messy CSVs merged into one clean database, an n8n flow with an LLM step on
> top, and a small audio app writing into the same database. I'll run all of it, then
> talk through the three things I found hardest."

*Scroll the README once, slowly. Don't read it aloud.*

---

### 0:20 – 1:20 — Task 1: the merge *(terminal)*

```bash
python -m src.pipeline
```

Talk over the output — it finishes in about a second:

> "One command rebuilds everything. 105 raw rows in, 102 ingested, 56 unique people
> out — 15 of them exist in all three systems.
>
> The hard part is there's no shared ID. Source one has email and phone, source two
> has email but no phone, source three has phone but no email. So source two and
> source three share nothing at all — they can only join through source one, or on
> something weak like name and city.
>
> So it's three tiers into a union-find, which makes the links transitive. Tier one,
> exact email. Tier two, last ten digits of the phone, after normalising five
> different formats. Tier three, fuzzy name plus canonical city — and that's the
> dangerous one."

*Point at the `merge methods` line as you say the tiers.*

---

### 1:20 – 2:20 — Hardest decision #1: making the matcher abstain

**The most important minute in the video. Slow down here.**

```bash
python -c "import sqlite3;[print(r[0]) for r in sqlite3.connect('data/consultbae.db').execute('SELECT reason FROM match_review')]"
```

> "Here's the one I got wrong first. There are three Arjun Mehtas in Noida — one in
> source one, one in source two, two in source three. One pair shares a phone number,
> so those are definitely the same person. The rest share only a name and a city.
>
> My first version merged the source-one and source-two records, because my ambiguity
> check only looked at the two sources being compared — it never saw that source three
> had two of them. Tests were green, nothing crashed. I only caught it by spot-checking
> every name that appears more than once and noticing one person had two completely
> different email addresses.
>
> The fix wasn't a better string metric — the names are character-for-character
> identical, no algorithm separates two real people with the same name in the same
> city. The fix was making the weak tier abstain. If any source puts more than one
> candidate into a name-and-city block, tier three doesn't fire in that block at all.
> And any weak merge that would land two different emails or two different phones on
> one person is rejected — because if they were one person, the email tier would
> already have matched them.
>
> Whatever it refuses goes to a match_review table for a human. The reasoning is
> asymmetric: a wrong merge is unrecoverable — two people's salary and project counts
> are now one row — but a missed merge is a five-second fix from a queue."

---

### 2:20 – 3:05 — Task 2: the n8n flow *(n8n tab)*

> "Task two. This flow pulls people from the merged database who have skills but no
> category, sends each skill list to an LLM, and writes a category back onto the same
> person row."

**Click Execute Workflow.** While it runs:

> "Two things I'd defend. The structured output parser — without the enum pinned to
> four values the model invents categories like 'AI/ML engineer' and my API rejects it.
> And the confidence gate: below 0.6 nothing gets written, the row goes to a review
> branch. A wrong category that looks confident is worse than a null, because
> recruiters filter on that field.
>
> It goes through my API rather than an SQLite node, because two processes holding
> write locks on one SQLite file is how you get 'database is locked' at the worst
> moment."

Open the **Write category back** node to show a 200, then:
```bash
curl -s localhost:8000/api/stats
```
> "And there's the tagged count going up."

---

### 3:05 – 4:05 — Task 3: the audio app *(submit page)*

Type a name, and **use a phone number already in the data — `09000000294`.**
Record, say one sentence, stop, submit.

> "Task three. Name, phone, then record in the browser or upload a file. I'm using a
> phone number that's already in the merged database, on purpose."

When the properties table appears:

> "On submit the server extracts everything the brief asked for — duration, sample
> rate, bitrate, loudness in dBFS — plus an SNR estimate, clipping percentage and a
> quality grade.
>
> And look at this line: linked to person 24, matched by phone. Same phone key the
> pipeline uses, so an existing applicant recording a clip attaches to their record
> instead of becoming a fifty-seventh person."

Switch to the submissions tab, refresh, play a clip for two seconds, then point at a
`poor` row:

> "Second view, every clip with a player and its properties. The quality grade is
> rules, not a model, deliberately — it says 'very noisy, SNR around 0.6 dB', so an
> ops person can tell a worker what to fix. A model that outputs 0.31 helps nobody."

---

### 4:05 – 4:50 — Hardest decisions #2 and #3

*Open `src/audio_features.py`, scrolled to `extract`.*

> "Second thing I got stuck on: bitrate. Duration, sample rate and loudness came off
> the decoded signal in twenty minutes. Then I kept computing bitrate as sample rate
> times bit depth times channels — 700 kbps for every file. Correct for a WAV,
> completely wrong for the WebM-Opus blob the browser actually uploads, where it should
> be about 32.
>
> What I had to accept is that once you decode to floats, the compression is gone.
> Bitrate is a property of the container, not the signal — no amount of numpy gets it
> back. So the extractor is two layers: ffprobe for container truth, decoded samples
> for signal truth. And when ffprobe reports no bitrate at all — which happens with
> Chrome recordings — I compute the real average from file size over duration instead
> of inventing a constant.
>
> Third one quickly: both HTML pages threw a 500 from inside Jinja's template cache
> while the whole API worked fine. It was the newer Starlette TemplateResponse
> signature — request first, then template name. Two-line fix. What I rejected was
> pinning Starlette backwards to keep a deprecated call alive."

---

### 4:50 – 5:30 — Data issues, scaling, close *(DATA_ISSUES.md)*

Scroll it while you talk:

> "Task four, the data issues report — 308 findings across 24 codes, every one logged
> in a table tied to the exact file and row, so the report can't drift from the
> pipeline. The ones I'd call out: a repeated header row in the middle of source three,
> a row in source two with rotated columns that turned out to be a duplicate once I
> repaired it, CTC stored in two different units in one column, and rates that are
> per-hour in some rows and per-month in others — those genuinely don't reconcile, so I
> flagged it rather than averaging it away.
>
> Task five is the launch one-pager. Short version: the synchronous ffmpeg call on the
> request thread breaks first, SQLite's write lock second, local disk third.
>
> That's everything — repo link's in the email. Thanks for watching."

---

## If you're running long, cut in this order

1. The third stuck-log item, the Jinja one — weakest of the three
2. Playing back a clip on the submissions page
3. The Task 5 sentence near the end
4. One of the two n8n justifications (keep the confidence gate, drop the SQLite one)

**Never cut:** the Arjun Mehta abstain story, the phone-matched link on submission,
or the n8n flow actually executing. Those three are what's being scored.

## What loses marks

- Reciting this flat. Talk, don't read.
- Silent gaps while something loads. Keep narrating.
- "I think" / "this should work". You built it — say what it does.
- An n8n flow that isn't running. A screenshot of a canvas scores zero.
- Going over six minutes.
