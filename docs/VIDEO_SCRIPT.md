# Video script — shot by shot

Everything below is literal: which window, where the cursor goes, what appears on
screen, and the exact words. Target **5:40**, hard ceiling **6:00**.

Two things this script won't do for you: it won't make you sound like you memorised it
(don't — know the beat, say it your way), and it can't answer the follow-up questions
on the call. Read `docs/DATA_ISSUES.md` and the stuck log in the README once before you
record. It shows in the voice.

---

# PART 0 — Setup before you press record

## 0.1 Screen and audio

| Setting | Value | Why |
|---|---|---|
| Resolution | 1440×900 (or 1080p) | Bigger and text is unreadable once compressed |
| Terminal font | 16–18pt | Reviewer watches this in a small window |
| Browser zoom | 100%, 110% for the n8n canvas | n8n nodes are tiny by default |
| Mic | Headset or phone earphones, close to mouth | Laptop mic in a room = echo |
| Notifications | macOS → Do Not Disturb | One WhatsApp popup = re-record |
| Recorder | Loom, or QuickTime / OBS | Loom gives you the shareable link directly |
| Capture | **Full screen**, not a single window | You switch between terminal, browser, editor |
| Face cam | Optional, small, bottom-right | Voice is required; face is not |

Do a 10-second test recording and play it back. Check: voice audible, terminal text
readable, cursor visible.

## 0.2 Windows to open before you record

Arrange these so you can switch with `Cmd+Tab` without hunting:

1. **Terminal A** — the app server, already running:
   ```bash
   cd consultbae-assignment && source .venv/bin/activate
   uvicorn app.main:app --port 8000
   ```
   Push this mostly off-screen. You never show it, it just has to stay alive.

2. **Terminal B** — the one you type in on camera. Big font, screen cleared.

3. **Chrome, 5 tabs, in this order:**
   1. `http://localhost:8000/` — submit page
   2. `http://localhost:8000/submissions`
   3. `http://localhost:5678` — n8n, flow **open on the canvas**
   4. `https://github.com/anaa-2562/consultbae-assignment`
   5. `https://github.com/anaa-2562/consultbae-assignment/blob/main/docs/DATA_ISSUES.md`

4. **VS Code** with `src/audio_features.py` open, scrolled to `extract`, and
   `src/matching.py` in a second tab.

5. A **short audio file on your Desktop** as a backup, in case the mic-permission
   dialog appears mid-take.

## 0.3 Reset the demo state

In terminal B, then `clear`:

```bash
rm -f data/consultbae.db* data/audio/2026*
python -m src.pipeline --quiet
python -m pytest -q          # confirm "57 passed"
clear
```

Then submit **one** clip through the app manually, so the submissions page isn't empty
when you reach it — you'll add a second one live on camera.

## 0.4 n8n — do this BEFORE recording, not during

- Flow imported from `n8n/consultbae_skill_tagger.json`
- `Config` node → `api_base` correct for your setup
- Chat-model credential added and **saved**
- Flow executed successfully **at least once**
- Then clear the tags so the flow has work to do on camera:
  ```bash
  python -c "import sqlite3;c=sqlite3.connect('data/consultbae.db');c.execute('UPDATE person SET skill_category=NULL');c.commit()"
  ```

Skip this and you will burn a take debugging an API key on video.

---

# PART 1 — The recording, shot by shot

Legend: **[SCREEN]** = what the viewer sees · **[DO]** = your action ·
**[SAY]** = your words.

---

## SHOT 1 · 0:00 – 0:20 · Intro

**[SCREEN]** Chrome tab 4 — the GitHub repo, scrolled to top, README visible.

**[DO]** Start recording. Sit still for one second before speaking (Loom clips the
first half-second). Then scroll the README slowly, about one screen, while talking.

**[SAY]**
> "Hi, I'm Dipanshu — this is my submission for the ConsultBae AI Automation
> assignment. Three messy CSVs merged into one clean database, an n8n flow with an LLM
> step on top, and a small audio app writing into the same database. I'll run all of
> it, then talk through the two things I found hardest."

**Do not** read the README aloud. It's a backdrop.

---

## SHOT 2 · 0:20 – 1:20 · Task 1, the merge

**[DO]** `Cmd+Tab` to terminal B. Type it live — typing reads as real, pasting reads
as prepared:

```bash
python -m src.pipeline
```

**[SCREEN]** Output appears in under a second:

```
raw rows read      : 105
  ingested         : 102
  skipped/rejected : 3
unique people      : 56
  in all 3 sources : 15
merge methods      : {'single_source': 25, 'email,phone': 16, 'phone': 11, 'name_city': 4}
data issues logged : 308 across 24 distinct codes
queued for review  : 1
```

**[SAY]** — start the moment you hit Enter, don't wait for output:
> "One command rebuilds everything. 105 raw rows in, 102 ingested, 56 unique people
> out — 15 of them in all three systems.
>
> The hard part is there's no shared ID. Source two has emails but no phones, source
> three has phones but no emails — so those two share nothing at all, and can only join
> through source one, or on something weak like name and city.
>
> Three tiers into a union-find, which makes the links transitive. Exact email. Then
> the last ten digits of the phone, after normalising five different formats. Then
> fuzzy name plus canonical city — and that third one is the dangerous one."

**[DO]** On "three tiers", move the cursor to the `merge methods` line and leave it
there. One deliberate move, no mouse-waving.

---

## SHOT 3 · 1:20 – 2:20 · Hardest decision #1 — making the matcher abstain

**The most important minute of the video. Slow down. Let the pauses land.**

**[DO]** Still terminal B:

```bash
python -c "import sqlite3;[print(r[0]) for r in sqlite3.connect('data/consultbae.db').execute('SELECT reason FROM match_review')]"
```

**[SCREEN]**
```
ambiguous name+city block: 'Arjun Mehta' in Noida appears 1x in source1, 1x in
source2, 2x in source3 - name+city cannot pick the right one
```

**[SAY]**
> "Here's the one I got wrong first. There are three Arjun Mehtas in Noida — one in
> source one, one in source two, and two in source three. One pair shares a phone
> number, so those two are definitely the same person. The rest share only a name and
> a city.
>
> My first version merged the source-one and source-two records, because my ambiguity
> check only looked at the two sources being compared — it never saw that source three
> had two of them. All the tests were green. Nothing crashed. I only caught it by
> spot-checking every name that appears more than once, and noticing that one person
> had two completely different email addresses.
>
> The fix wasn't a better string metric. The names are character-for-character
> identical — no algorithm separates two real people with the same name in the same
> city. The fix was making the weak tier abstain. If any source puts more than one
> candidate into a name-and-city block, tier three doesn't fire in that block at all.
> And any weak merge that would land two different emails, or two different phones, on
> one person gets rejected — because if they were one person, the email tier would
> already have matched them.
>
> Whatever it refuses goes to a review table for a human. The reasoning is asymmetric:
> a wrong merge is unrecoverable — two people's salary and project counts are now one
> row and you can't unpick it — but a missed merge is a five-second fix from a queue."

**[DO]** *Optional, if on time:* switch to VS Code → `src/matching.py` →
`_ambiguity_reason`, let the three vetoes sit on screen for ~3 seconds while you
finish. Don't read code aloud.

---

## SHOT 4 · 2:20 – 3:05 · Task 2, the n8n flow

**[DO]** Chrome tab 3 (n8n canvas). Whole flow visible — press `1` to zoom-to-fit
before you start talking.

**[SAY]**
> "Task two. This flow pulls people out of the merged database who have skills but no
> category yet, sends each skill list to an LLM, and writes a category back onto the
> same person row."

**[DO]** Click **Execute Workflow**. Keep talking while the green checkmarks light up
node by node — that animation is the proof it ran.

**[SAY]**
> "Two things I'd defend here. The structured output parser — without the enum pinned
> to four values, the model invents categories like 'AI/ML engineer' and my API rejects
> it with a 422. And the confidence gate: below 0.6 nothing gets written, the row goes
> to a review branch instead. A wrong category that looks confident is worse than a
> null, because recruiters filter on this field."

**[DO]** Double-click the **Write category back** node → output panel opens → point at
the `200` / returned JSON → `Esc`.

**[DO]** `Cmd+Tab` to terminal B:
```bash
curl -s localhost:8000/api/stats
```
**[SCREEN]** `{"people":56,...,"tagged":43}`

**[SAY]**
> "And there's the tagged count going up in the database."

---

## SHOT 5 · 3:05 – 4:05 · Task 3, the audio app

**[DO]** Chrome tab 1. Type into the fields on camera:

- Name: `Rohit Verma`
- Phone: `09000000294` ← **this exact number matters, it already exists in the data**

**[SAY]**
> "Task three. Name, phone, and then either record in the browser or upload a file.
> I'm using a phone number that's already in the merged database — on purpose."

**[DO]** Click **● Record**, say one clear sentence (*"This is a test recording for the
ConsultBae audio collection app."*), click **■ Stop**, then **Submit recording**.

**[SCREEN]** The "Extracted properties" table: duration, sample rate, bitrate,
loudness, peak, SNR, clipping, near-silence, codec/channels, quality pill, and
**"Linked to person · #24 (phone)"**.

**[SAY]**
> "The server pulls out everything the brief asked for — duration, sample rate,
> bitrate, loudness in dBFS — plus an SNR estimate, clipping percentage and a quality
> grade.
>
> And look at this line: linked to person 24, matched by phone. It used the same phone
> key the pipeline uses, so an existing applicant recording a clip attaches to their
> record instead of becoming a fifty-seventh person."

**[DO]** Put the cursor on the "Linked to person" row as you say it. That one line is
what proves Task 3 is wired into Task 1 — it must be on screen and pointed at.

**[DO]** Chrome tab 2, refresh. Play a clip for two seconds, then move the cursor to a
row with a red **POOR** pill.

**[SAY]**
> "Second view — every clip with a player and its extracted properties. The quality
> grade is rules, not a model, deliberately: it says 'very noisy, SNR around 0.6 dB',
> so an ops person can tell a worker exactly what to fix. A model that outputs 0.31
> helps nobody at 2am."

---

## SHOT 6 · 4:05 – 4:40 · Hardest decision #2 — bitrate

**[DO]** VS Code, `src/audio_features.py`, scrolled so `extract` and the `_ffprobe`
call are both visible. Scroll slowly, once.

**[SAY]**
> "Second thing I got stuck on: bitrate. Duration, sample rate and loudness came off
> the decoded signal in about twenty minutes. Then I kept computing bitrate as sample
> rate times bit depth times channels — which gives 700 kbps for every file. That's
> right for a WAV and completely wrong for the WebM-Opus blob the browser actually
> uploads, where it should be about 32.
>
> What I had to accept is that once you decode to floats, the compression is gone —
> bitrate is a property of the container, not the signal. So the extractor is two
> layers: ffprobe for container truth, decoded samples for signal truth. And when
> ffprobe reports no bitrate at all — which happens with Chrome recordings, because
> MediaRecorder writes a live-stream container with no duration header — I compute the
> real average from file size over duration instead of inventing a constant."

---

## SHOT 7 · 4:40 – 5:25 · Data issues, scaling, close

**[DO]** Chrome tab 5 — `DATA_ISSUES.md` on GitHub. Scroll steadily through the
headings while you talk. Don't stop to read anything.

**[SAY]**
> "Task four — the data issues report. 308 findings across 24 issue codes, and every
> one is logged in a table tied to the exact file and row, so the report can't drift
> from the pipeline. The ones I'd call out: a repeated header row in the middle of
> source three, a row in source two with rotated columns that turned out to be a
> duplicate once I repaired it, CTC stored in two different units in the same column,
> and rates that are per-hour in some rows and per-month in others — those genuinely
> don't reconcile, so I flagged that rather than averaging it away.
>
> Task five is the launch one-pager: what breaks at five thousand workers. Short
> version — the synchronous ffmpeg call on the request thread breaks first, SQLite's
> write lock second, local disk storage third.
>
> That's everything. Repo link's in the email — thanks for watching."

**[DO]** Stop talking, wait one full second, stop the recording. Don't end on
"umm… yeah… that's it."

---

# PART 2 — Optional extras

Add these **only** if the take came in under 5:30. Never at the cost of going over 6.

**A. Third stuck-log item** (~15s, after Shot 6):
> "Third, quickly: both HTML pages threw a 500 from inside Jinja's template cache
> while the whole API worked fine. It was the newer Starlette TemplateResponse
> signature — request first, then template name. Two-line fix. What I rejected was
> pinning Starlette backwards to keep a deprecated call working."

**B. The SQLite-lock reason** (~10s, in Shot 4):
> "It goes through my API rather than an SQLite node, because two processes holding
> write locks on one SQLite file is how you get 'database is locked' at the worst
> possible moment."

**C. Tests** (~8s, after Shot 3): run `python -m pytest -q`, let `57 passed` show.

---

# PART 3 — If you're running long, cut in this order

1. Extras A, B, C (already optional)
2. Playing back a clip on the submissions page (Shot 5)
3. The `curl /api/stats` check (Shot 4) — the green checkmarks already proved it ran
4. The Task 5 sentence (Shot 7)

**Never cut:** the Arjun Mehta abstain story (Shot 3), the "linked to person 24,
matched by phone" line (Shot 5), or the n8n flow actually executing (Shot 4). Those
three are what's being scored.

---

# PART 4 — What loses marks

- **Reciting this flat.** Talk. Slightly rough and confident beats polished and read.
- **Silence while something loads.** Keep narrating — that's why every shot has you
  talking *over* the action.
- **"I think…", "this should work…"**. You built it — say what it does.
- **An n8n flow that isn't running.** A screenshot of a canvas scores zero.
- **"The AI wrote this."** AI use is allowed and it's in the README; on camera you
  explain your system.
- **Going over six minutes.** They wrote max 6. Twenty seconds under is a signal.
- **Unreadable terminal text.** The most common failure in take-home videos.

---

# PART 5 — Take checklist

Watch your own recording once with headphones before uploading:

- [ ] Voice audible throughout, no clipping
- [ ] Terminal text readable at normal playback size
- [ ] The pipeline output block was actually on screen
- [ ] The `match_review` line was visible during the Arjun Mehta story
- [ ] n8n nodes visibly went green
- [ ] "Linked to person #24 (phone)" was on screen and pointed at
- [ ] Submissions list with play buttons appeared
- [ ] Under 6:00
- [ ] No notification popups, no personal tabs visible

Then: Loom → Share → **anyone with the link can view** (check it in an incognito
window — a private Loom link scores the same as no video), and reply to Parul's email
with the repo link and the video link.
