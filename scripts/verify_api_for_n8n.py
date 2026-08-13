"""Exercise the endpoints the n8n flow calls, without needing an LLM key.

    python scripts/verify_api_for_n8n.py --base http://localhost:8000

This is a TEST HARNESS for the API surface, not the Task 2 deliverable. Task 2 is
the n8n flow in n8n/consultbae_skill_tagger.json; this script exists so you can
prove GET /api/people and PATCH /api/people/{id}/skill-category behave before you
wire credentials into n8n, and so a reviewer without an OpenAI key can still see
the write-back path work.

The classifier here is a deliberately dumb keyword vote - the real one is the LLM
node inside n8n.
"""
from __future__ import annotations

import argparse
import json
import urllib.request

AUTOMATION = {"n8n", "zapier", "langchain", "make", "selenium", "web scraping"}
WEB = {"react", "javascript", "fastapi", "rest apis", "docker"}
DATA = {"sql", "mysql", "mongodb", "pandas"}


def _req(url: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def classify(skills: str) -> tuple[str, float]:
    toks = {s.strip().lower() for s in (skills or "").split(",") if s.strip()}
    scores = {
        "automation-heavy": len(toks & AUTOMATION),
        "web-dev": len(toks & WEB),
        "data": len(toks & DATA),
    }
    total = sum(scores.values()) or 1
    best, hits = max(scores.items(), key=lambda kv: kv[1])
    share = hits / total
    if hits == 0 or share < 0.45:
        return "generalist", round(share, 2)
    return best, round(share, 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min-confidence", type=float, default=0.45)
    args = ap.parse_args()

    people = _req(f"{args.base}/api/people?untagged=true&limit={args.limit}")
    print(f"{len(people)} untagged people")

    written = skipped = 0
    for p in people:
        cat, conf = classify(p.get("skills") or "")
        if conf < args.min_confidence:
            skipped += 1
            print(f"  skip  #{p['person_id']:<3} {p['full_name']:<18} conf={conf} -> human review")
            continue
        _req(
            f"{args.base}/api/people/{p['person_id']}/skill-category",
            method="PATCH",
            body={"skill_category": cat, "confidence": conf, "tagged_by": "verify-script"},
        )
        written += 1
        print(f"  tag   #{p['person_id']:<3} {p['full_name']:<18} {cat} ({conf})")

    print(f"\nwritten: {written}   left for review: {skipped}")
    print("stats:", json.dumps(_req(f"{args.base}/api/stats")))


if __name__ == "__main__":
    main()
