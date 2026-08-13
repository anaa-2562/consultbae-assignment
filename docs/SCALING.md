# Task 5 — 5,000 gig workers, one weekend. What breaks first?

Assume the launch shape: a WhatsApp blast on Saturday morning, most traffic in two
spikes (Sat 10:00–13:00, Sun 19:00–22:00), ~60% on mid-range Android over mobile
data, clips of 30–120 seconds, and a long tail of people retrying because they
weren't sure it worked.

Rough volume: 5,000 workers × ~1.3 attempts × ~45 s of Opus audio ≈ **6,500 files,
~3.5 GB, ~80 hours of audio** — but arriving in maybe 90 concentrated minutes, so
peak is ~2–3 uploads/second with a long tail of slow mobile connections holding
connections open.

---

## What breaks, in the order it will actually break

### 1. Synchronous analysis on the request thread — breaks first, within minutes
Today `POST /api/submissions` writes the file, then shells out to ffmpeg/ffprobe
**before responding**. That's ~0.5–2 s of CPU per clip. At 3 uploads/second on a
small box the workers saturate, the queue backs up, mobile clients time out at 30 s,
and the retries make it worse — the classic upload-storm death spiral.

**Change before launch:** return `202 Accepted` as soon as the bytes are stored and
the row exists. Push `(submission_id, storage_key)` onto a queue (Redis/RQ, Celery,
or SQS) and have 2–4 workers fill in duration/sample-rate/bitrate/loudness
afterwards. The submissions view shows `status = processing` until they land. This
also makes ffmpeg failures retryable instead of user-visible.

### 2. SQLite — breaks second, during the first spike
One writer at a time. WAL mode helps readers, but concurrent submission INSERTs plus
the analysis workers' UPDATEs will produce `database is locked` under a spike, and
the file lives on one machine's disk so you cannot scale out.

**Change before launch:** move to Postgres (managed — Neon/Supabase/RDS). The schema
ports as-is; `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `BIGSERIAL` and the boolean
columns become real booleans. Keep SQLite for local dev.

### 3. Local disk for audio — breaks third, and loses data when it does
`data/audio/` on the app server means: the disk fills (3.5 GB is fine, 30 GB after a
month is not), files vanish on a container redeploy (Render/Railway filesystems are
ephemeral), and you cannot run two app instances because each has half the files.

**Change before launch:** S3/R2/GCS with **presigned direct uploads** — the browser
PUTs straight to object storage and only tells the API the key. That removes the
upload bytes from the app entirely, which is what makes horizontal scaling possible.
Store `storage_key`, not a local path. Lifecycle rule: Standard → Infrequent Access
at 30 days.

### 4. Duplicates — the quiet failure, worst on Sunday
Three kinds, and only one is currently handled:

- **Same file twice** (impatient double-tap): handled — SHA-256 already flags it.
- **Same person, two attempts, different audio**: allowed today. Do we keep both?
  For a paid gig task, "one accepted submission per person per task" needs to be a
  DB constraint, not a convention — otherwise payouts double-count.
- **Same person, different phone number** (borrowed handset, typo, `+91` vs `0`):
  the phone key handles formatting, but a genuinely different number creates a
  second person. At 5,000 workers this is the expensive one, because it's a payments
  problem, not a data problem.

**Change before launch:** unique index on `(person_id, task_id)` for accepted
submissions; OTP-verify the phone number at first submission so the phone is a real
identifier rather than a typed string. Route unresolved identities into the same
`match_review` queue the pipeline already writes to.

### 5. Browser recording on real Android phones
`MediaRecorder` mime support differs (Chrome → `audio/webm;codecs=opus`, Safari/iOS →
`audio/mp4`), a backgrounded tab stops the recorder mid-clip, and a dropped mobile
connection mid-POST leaves a truncated file that ffprobe still parses — producing a
"valid" 4-second clip of someone's 90-second answer.

**Change before launch:** send the client-measured duration alongside the file and
reject when the server-measured duration differs by more than ~10% (that check
catches truncation, which a size check does not). Cap uploads at 25 MB (already
enforced) and show recording length prominently so people don't submit 3-second
clips.

### 6. Cost — not what breaks, but worth knowing
Storage is trivially cheap (~$0.08/month for 3.5 GB on R2/S3). **Egress is the
trap**: if a QA team streams every clip back for review, 3.5 GB × several passes at
S3's ~$0.09/GB adds up — Cloudflare R2's zero egress is the obvious answer if
reviewers stream a lot. The real cost is compute for analysis, and that's bounded
by the worker count you choose.

---

## The pre-launch list, ranked by (risk × cheapness to fix)

| # | Change | Why now | Effort |
|---|---|---|---|
| 1 | Async analysis via a queue, return 202 | Stops the spike from taking the app down | ~half a day |
| 2 | Object storage + presigned direct upload | Data loss on redeploy is unrecoverable | ~half a day |
| 3 | Postgres instead of SQLite | Write locks under concurrency | ~2 hours (schema ports cleanly) |
| 4 | OTP-verify phone + unique accepted submission per person/task | Duplicate payouts | ~1 day |
| 5 | Client-vs-server duration check | Silent truncation is invisible otherwise | ~1 hour |
| 6 | Rate limit per phone/IP + basic abuse cap | One script can fill the bucket | ~1 hour |
| 7 | Dashboard: submissions/min, %failed analysis, quality mix | You cannot fix what you cannot see during the weekend | ~2 hours |

## What I would *not* change before launch

- **Rewriting the quality scorer into an ML model.** The rule-based grade is
  explainable ("too quiet, −38 dBFS"), which is what an ops person needs to tell a
  worker how to fix their recording. A model that says `0.31` helps no one at 2am.
- **Transcription / language detection.** Real value later, pure risk during a
  launch weekend — it multiplies cost per clip and adds an external dependency in
  the critical path.
- **A polished UI.** Failure here is technical, not cosmetic.

## What I'd watch live over the weekend

Uploads/minute vs. analysis-queue depth (if the second grows faster than the first,
add workers), the `poor` quality share (a spike means the instructions are wrong,
not the code), 4xx vs 5xx split, and the count of new people created per hour — a
jump there means phone matching is failing and payouts are about to get messy.
