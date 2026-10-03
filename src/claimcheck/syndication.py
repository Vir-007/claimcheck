"""Syndication detection: which "independent" sources are really one story?

Forty outlets agreeing means little if thirty-nine of them ran the same PTI
wire copy. We group sources into clusters with union-find, linking two sources
when any of these rules fires:

1. **Wire copy**: headlines/snippets are near-identical (similarity >= 0.6)
   *and* both were published within 72 hours of each other. If both carry the
   same wire-agency byline (PTI, ANI, IANS, Reuters...), a looser 0.45 suffices.
2. **Publisher network**: same registrable domain (timesofindia.indiatimes.com
   and bangaloremirror.indiatimes.com) and similarity >= 0.5, at any time.
3. **Copy-paste forward**: text is essentially verbatim (>= 0.85) regardless of
   date. This is how WhatsApp forwards look on the open web: the same message
   reposted on Facebook, Brainly and blogs for years.
4. **Undated pair**: if either side lacks a usable date, require >= 0.75.

Similarity is the max of token Jaccard and a length-guarded containment score
over normalised text (lower-cased, number words -> digits, outlet suffixes and
possessives stripped), so "7 killed, 19 injured as truck hits pickup van in
Maharashtra" matches "Seven killed, 19 injured as truck hits pickup van in
Maharashtra's Amravati".

Clustering is **complete-linkage**: a source joins a cluster only if it links to
*every* member, which stops chains like hoax-post -> fact-check -> other
fact-check from snowballing into one giant cluster. Sources with opposite
stances (supports vs contradicts) are never linked: a copy of a story cannot
disagree with itself.

Each cluster then counts as **one** independent confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .normalize import Source, detect_wire

WIRE_WINDOW_H = 72
T_WIRE = 0.6
T_WIRE_SAME_AGENCY = 0.45
T_PUBLISHER = 0.5
T_VERBATIM = 0.85
T_UNDATED = 0.75

_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
        "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
_STOP = {"the", "a", "an", "of", "in", "on", "as", "to", "and", "for", "by", "at", "is", "was", "with",
         "after", "from", "s", "news", "latest", "update", "updates"}

# Second-level suffixes where the registrable domain has three labels.
_SLD = {"co.in", "co.uk", "com.au", "org.in", "gov.in", "net.in", "ac.in", "com.pk", "co.za"}


def registrable_domain(domain: str) -> str:
    parts = domain.lower().split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def norm_text(text: str, outlet: str = "") -> list[str]:
    t = text or ""
    if outlet:
        t = re.sub(rf"\s[-|–]\s*{re.escape(outlet)}\s*$", "", t, flags=re.I)
    t = re.sub(r"\s[|]\s[^|]{0,40}$", "", t)  # " | Mumbai news"
    t = t.lower().replace("’", "'")
    t = re.sub(r"'s\b", "", t)
    toks = re.findall(r"[a-z0-9]+", t)
    out = []
    for tok in toks:
        tok = _NUM.get(tok, tok)
        if tok in _STOP:
            continue
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return out


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def similarity(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    small = min(len(sa), len(sb))
    # Containment: a shorter headline wholly inside a longer one. Guarded so
    # that a short title cannot "contain" itself into a match.
    contain = len(sa & sb) / small if small >= 7 else 0.0
    return max(jaccard(a, b), 0.9 * contain)


def _precise(s: Source) -> bool:
    """Dates good to the day or better (not Scholar's bare year, not '3 years ago')."""
    if s.published is None or not s.date_text:
        return False
    if re.fullmatch(r"\d{4}", s.date_text.strip()):
        return False
    m = re.search(r"(\d+)\s+(month|year)s?\s+ago", s.date_text)
    return m is None


def _hours(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 3600


@dataclass
class Link:
    i: int
    j: int
    rule: str
    sim: float


@dataclass
class Cluster:
    members: list[int]
    rule: str  # wire | publisher | copy-paste | undated | single
    wire: str | None = None
    links: list[Link] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)


_OPPOSED = {frozenset(("supports", "contradicts"))}


def pair_link(a: Source, b: Source, stance_a: str | None = None, stance_b: str | None = None) -> Link | None:
    """Decide whether two sources are the same underlying story. Returns the rule fired."""
    if stance_a and stance_b and frozenset((stance_a, stance_b)) in _OPPOSED:
        return None
    ta, tb = norm_text(a.title, a.outlet), norm_text(b.title, b.outlet)
    sa, sb = norm_text(a.snippet), norm_text(b.snippet)
    has_snips = len(sa) >= 10 and len(sb) >= 10
    sim = max(similarity(ta, tb), similarity(sa, sb) if has_snips else 0.0)
    if sim < min(T_WIRE_SAME_AGENCY, T_PUBLISHER):
        return None
    # Verbatim needs real overlap of the whole text, not containment.
    verbatim = max(jaccard(ta, tb) if min(len(ta), len(tb)) >= 5 else 0.0, jaccard(sa, sb) if has_snips else 0.0)
    if verbatim >= T_VERBATIM:
        return Link(-1, -1, "copy-paste", verbatim)
    same_pub = registrable_domain(a.domain) == registrable_domain(b.domain) and a.kind != "social"
    if same_pub and sim >= T_PUBLISHER:
        return Link(-1, -1, "publisher", sim)
    if _precise(a) and _precise(b):
        if _hours(a.published, b.published) <= WIRE_WINDOW_H:
            wa, wb = a.wire, b.wire
            if sim >= T_WIRE or (wa and wa == wb and sim >= T_WIRE_SAME_AGENCY):
                return Link(-1, -1, "wire", sim)
        return None
    if sim >= T_UNDATED:
        return Link(-1, -1, "undated", sim)
    return None


def cluster(sources: list[Source], stances: list[str] | None = None) -> list[Cluster]:
    n = len(sources)
    st = stances or [None] * n
    pair: dict[tuple[int, int], Link] = {}
    for i in range(n):
        for j in range(i + 1, n):
            lk = pair_link(sources[i], sources[j], st[i], st[j])
            if lk:
                lk.i, lk.j = i, j
                pair[(i, j)] = lk

    # Complete-linkage agglomeration, strongest links first.
    owner = list(range(n))
    groups_by_id: dict[int, list[int]] = {i: [i] for i in range(n)}
    for (i, j), lk in sorted(pair.items(), key=lambda kv: -kv[1].sim):
        gi, gj = owner[i], owner[j]
        if gi == gj:
            continue
        if all((min(a, b), max(a, b)) in pair for a in groups_by_id[gi] for b in groups_by_id[gj]):
            for k in groups_by_id[gj]:
                owner[k] = gi
            groups_by_id[gi] += groups_by_id.pop(gj)
    links = list(pair.values())
    parent = owner

    def find(x: int) -> int:
        return parent[x]

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    out: list[Cluster] = []
    for members in groups.values():
        members.sort(key=lambda k: (sources[k].published is None,
                                    sources[k].published.timestamp() if sources[k].published else 0, k))
        mset = set(members)
        clinks = [lk for lk in links if lk.i in mset and lk.j in mset]
        if len(members) == 1:
            rule = "single"
        else:
            # Name the cluster after its strongest evidence of shared origin.
            rules = {lk.rule for lk in clinks}
            rule = next(r for r in ("wire", "publisher", "copy-paste", "undated") if r in rules)
            if all(sources[k].kind == "social" for k in members):
                rule = "copy-paste"  # social reposts are forwards, not wire copy
        wire = next((sources[k].wire for k in members if sources[k].wire), None)
        if wire is None and len(members) > 1:
            wire = detect_wire(*(sources[k].snippet for k in members))
        out.append(Cluster(members, rule, wire, clinks))
    out.sort(key=lambda c: (-c.size, c.members[0]))
    return out
