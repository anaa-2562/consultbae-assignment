"""Identity resolution across the three sources.

No shared ID exists. What we have:

    source1 (naukri)  : name, EMAIL, PHONE, city, ...
    source2 (gig)     : name, EMAIL,   -   , city, ...
    source3 (cbnexus) : name,   -   , PHONE, city, ...

So source2 and source3 share *no* strong identifier at all. They can only be
linked transitively through source1, or by a weak (name + city) signal.

Strategy - three tiers, strongest first, unioned with a disjoint-set structure
so that A=B and B=C implies A=C:

  Tier 1  EMAIL_EXACT   normalised email equality           confidence 1.00
  Tier 2  PHONE_EXACT   last-10-digit equality              confidence 0.99
  Tier 3  NAME_CITY     fuzzy name + canonical city         confidence 0.80
                        -- only applied between records that are still
                           unlinked, and only when it is UNAMBIGUOUS.

Tier 3 is deliberately conservative. It refuses to merge and writes to
`match_review` when:
  * the (surname, city) block contains more than one record from the same
    source (e.g. two `Arjun Mehta`s in Noida in source3 - one of them is a
    different human), or
  * the merge would put two different emails *from the same source* or two
    different phone numbers into one person.

That last guard is what stops the `Deepak Nair` trap (Bengaluru vs Delhi) and
the `Arjun Mehta` trap from silently collapsing two real people into one.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz

from .normalize import email_key, name_key, name_parts, phone_key

TIER_CONFIDENCE = {"email": 1.0, "phone": 0.99, "name_city": 0.80}
NAME_SIM_THRESHOLD = 88  # rapidfuzz token_sort_ratio


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)
        self.rank.setdefault(x, 0)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for x in self.parent:
            out.setdefault(self.find(x), []).append(x)
        return out


@dataclass
class Record:
    """One normalised row, before merging."""

    rid: str            # 's1:12'
    source: str         # 'source1'
    row: int
    name: str
    email: str | None
    phone: str | None
    city: str | None
    payload: dict = field(default_factory=dict)

    @property
    def ekey(self) -> str | None:
        return email_key(self.email)

    @property
    def pkey(self) -> str | None:
        return phone_key(self.phone)


@dataclass
class MatchResult:
    clusters: list[list[Record]]
    methods: dict[str, set[str]]        # cluster root -> {'email','phone',...}
    reviews: list[dict]
    links: list[tuple[str, str, str, float]]   # (rid_a, rid_b, method, confidence)


def names_compatible(a: str, b: str) -> bool:
    """True when two name strings plausibly denote the same person.

    Handles the abbreviated-first-name case ("R. Verma" vs "Rohit Verma") that a
    plain string ratio scores poorly.
    """
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    fa, sa = name_parts(a)
    fb, sb = name_parts(b)
    if sa and sa == sb:
        # same surname: accept if one first name is an initial of the other
        if fa and fb and (fa[0] == fb[0]) and (len(fa) == 1 or len(fb) == 1):
            return True
    return fuzz.token_sort_ratio(ka, kb) >= NAME_SIM_THRESHOLD


def resolve(records: Iterable[Record]) -> MatchResult:
    recs = list(records)
    by_rid = {r.rid: r for r in recs}
    uf = UnionFind()
    for r in recs:
        uf.add(r.rid)

    methods: dict[str, set[str]] = {}
    links: list[tuple[str, str, str, float]] = []
    reviews: list[dict] = []

    def link(a: str, b: str, method: str) -> None:
        merged = uf.union(a, b)
        links.append((a, b, method, TIER_CONFIDENCE[method]))
        root = uf.find(a)
        methods.setdefault(root, set()).add(method)
        if merged:
            # methods recorded against the old roots must follow the new root
            for k in (a, b):
                for m in methods.pop(k, set()) if k != root else set():
                    methods[root].add(m)

    # ---- Tier 1: email ----------------------------------------------------
    by_email: dict[str, list[Record]] = {}
    for r in recs:
        if r.ekey:
            by_email.setdefault(r.ekey, []).append(r)
    for _, group in by_email.items():
        for other in group[1:]:
            link(group[0].rid, other.rid, "email")

    # ---- Tier 2: phone ----------------------------------------------------
    by_phone: dict[str, list[Record]] = {}
    for r in recs:
        if r.pkey:
            by_phone.setdefault(r.pkey, []).append(r)
    for _, group in by_phone.items():
        for other in group[1:]:
            link(group[0].rid, other.rid, "phone")

    # ---- Tier 3: name + city (conservative) -------------------------------
    # Block on (surname, city) to keep comparisons cheap and local.
    blocks: dict[tuple[str, str], list[Record]] = {}
    for r in recs:
        _, surname = name_parts(r.name)
        if surname and r.city:
            blocks.setdefault((surname, r.city), []).append(r)

    for (surname, city), group in blocks.items():
        if len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if uf.find(a.rid) == uf.find(b.rid):
                    continue                      # already the same person
                if a.source == b.source:
                    continue                      # within-source dupes are handled by email/phone
                if not names_compatible(a.name, b.name):
                    continue

                ambiguous = _ambiguity_reason(a, b, group, uf, by_rid)
                if ambiguous:
                    # One review per ambiguous block, listing every candidate,
                    # rather than one per rejected pair.
                    key = (ambiguous, surname, city)
                    existing = next((r for r in reviews if r["_key"] == key), None)
                    if existing is None:
                        reviews.append(
                            {
                                "_key": key,
                                "reason": ambiguous,
                                "candidates": [_summary(a), _summary(b)],
                                "block": {"surname": surname, "city": city},
                            }
                        )
                    else:
                        for cand in (a, b):
                            if not any(c["rid"] == cand.rid for c in existing["candidates"]):
                                existing["candidates"].append(_summary(cand))
                    continue
                link(a.rid, b.rid, "name_city")

    for rv in reviews:
        rv.pop("_key", None)
        rv["candidates"].sort(key=lambda c: (c["source"], c["row"]))

    clusters = [[by_rid[rid] for rid in members] for members in uf.groups().values()]
    # stable ordering: by lowest source/row in each cluster
    for c in clusters:
        c.sort(key=lambda r: (r.source, r.row))
    clusters.sort(key=lambda c: (c[0].source, c[0].row))
    resolved_methods = {}
    for c in clusters:
        root = uf.find(c[0].rid)
        resolved_methods[root] = methods.get(root, set())
    return MatchResult(clusters=clusters, methods=resolved_methods, reviews=reviews, links=links)


def _cluster_members(rid: str, uf: UnionFind, by_rid: dict[str, Record]) -> list[Record]:
    root = uf.find(rid)
    return [r for r in by_rid.values() if uf.find(r.rid) == root]


def _ambiguity_reason(a: Record, b: Record, block: list[Record], uf: UnionFind, by_rid) -> str | None:
    """Return a reason string when this weak match must NOT be auto-applied.

    Three independent vetoes, all of which fire on the planted `Arjun Mehta`
    trap and none of which fire on the genuine source2<->source3 links.
    """
    # (1) the whole (name, city) block is ambiguous if ANY source contributes
    #     more than one name-compatible record to it. Two `Arjun Mehta`s in
    #     Noida inside source3 means no `Arjun Mehta` in Noida can be matched
    #     on name+city alone - not even the source1<->source2 pair.
    compatible = [r for r in block if names_compatible(r.name, a.name)]
    per_source = Counter(r.source for r in compatible)
    if any(n > 1 for n in per_source.values()):
        return (
            f"ambiguous name+city block: '{a.name}' in {a.city} appears "
            + ", ".join(f"{n}x in {s}" for s, n in sorted(per_source.items()))
            + " - name+city cannot pick the right one"
        )

    # (2) two different email addresses. If these were one person the email tier
    #     would already have linked them, so distinct emails are evidence
    #     AGAINST the match, not neutral.
    merged = _cluster_members(a.rid, uf, by_rid) + _cluster_members(b.rid, uf, by_rid)
    emails = {r.ekey for r in merged if r.ekey}
    if len(emails) > 1:
        return f"merge would put {len(emails)} distinct emails on one person: {sorted(emails)}"

    # (3) two different phone numbers - same argument as (2) for the phone tier.
    phones = {r.pkey for r in merged if r.pkey}
    if len(phones) > 1:
        return f"merge would put {len(phones)} distinct phone numbers on one person: {sorted(phones)}"
    return None


def _summary(r: Record) -> dict:
    return {
        "rid": r.rid,
        "source": r.source,
        "row": r.row,
        "name": r.name,
        "email": r.email,
        "phone": r.phone,
        "city": r.city,
    }


def reviews_to_json(reviews: list[dict]) -> str:
    return json.dumps(reviews, indent=2, sort_keys=True)
