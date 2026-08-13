# Task 2 — n8n flow: LLM skill-category tagger

`consultbae_skill_tagger.json` is the exported workflow. Import it with
**n8n → Workflows → ⋯ → Import from File**.

## What it does

```
Manual / Webhook trigger
      → Config (api_base, batch_limit, min_confidence)
      → GET  /api/people?untagged=true          (people from the merged DB, no category yet)
      → IF   has skills
      → LLM chain + Structured Output Parser    (enum: automation-heavy | web-dev | data | generalist)
      → Code: validate enum + confidence
      → IF   confidence >= 0.6
           ├─ true  → PATCH /api/people/{id}/skill-category   → Aggregate → Respond
           └─ false → Collect for human review                (nothing is written)
```

The category lands in `person.skill_category` in the **same** SQLite database the
Task 1 pipeline builds, alongside `skill_category_conf`, `skill_category_by` and
`skill_category_at`.

## Why it goes through the API instead of an SQLite node

n8n *does* have a SQLite/DB node, but pointing it at the same file the FastAPI app
writes to means two processes holding write locks on one SQLite file — the classic
`database is locked` failure. The app already owns the DB, so the flow talks HTTP and
the server re-validates the enum. A broken flow cannot corrupt the column.

## Running it

1. Start the app so n8n has something to call:

   ```bash
   uvicorn app.main:app --port 8000
   ```

2. Start n8n:

   ```bash
   docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n
   ```

3. Fix `api_base` in the **Config** node for your setup:

   | n8n runs as | `api_base` |
   |---|---|
   | Docker (above) | `http://host.docker.internal:8000` |
   | `npx n8n` on the same machine | `http://localhost:8000` |
   | n8n Cloud | your `ngrok http 8000` https URL |

   On Linux Docker, `host.docker.internal` needs
   `--add-host=host.docker.internal:host-gateway` on the `docker run` line.

4. Add a credential on **OpenAI Chat Model** (or swap that node for Ollama / Groq /
   Gemini — any chat-model node connects to the same `ai_languageModel` input).

5. Hit **Execute Workflow**, or activate it and call the webhook:

   ```bash
   curl -X POST http://localhost:5678/webhook/consultbae/tag-skills
   ```

6. Confirm the write-back:

   ```bash
   curl -s localhost:8000/api/stats            # "tagged" goes up
   python3 -c "import sqlite3;print(sqlite3.connect('data/consultbae.db').execute(
     'SELECT skill_category, COUNT(*) FROM person GROUP BY 1').fetchall())"
   ```

## Decisions worth defending

- **Structured Output Parser, not free text.** Without the enum schema the model
  happily returns `AI/ML` or `Automation Engineer`, and the API rejects it with a 422.
  The parser makes the model retry against the schema instead.
- **Confidence gate.** A wrong category that looks confident is worse than a null —
  recruiters filter on this field. Below 0.6 the row is collected for a human and the
  DB is left untouched.
- **temperature 0.** The same person classified twice gets the same answer, so a re-run
  after a failure is not a re-roll.
- **Validation in code as well as in the parser.** The parser is a prompt-level
  constraint; the Code node and the API are the actual guarantees.

## If you have no LLM key handy

`scripts/verify_api_for_n8n.py` exercises the exact endpoints the flow calls
(`GET /api/people?untagged=true` → `PATCH …/skill-category`) with a rule-based
stand-in classifier. It is a **test harness for the API surface, not the Task 2
deliverable** — the deliverable is the flow above running in n8n.
