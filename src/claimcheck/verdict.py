"""Collapse per-source stances into independent voices and score the claim."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from .normalize import Source
from .stance import CONTRADICTS, PARTIAL, SUPPORTS, UNRELATED, Stance
from .syndication import Cluster, cluster, registrable_domain

# How much one independent voice of each kind counts. Fact-checkers and peer
# review outrank a random Facebook post; this is deliberately simple and shown
# to the user rather than hidden.
KIND_WEIGHT = {"factcheck": 1.5, "scholar": 1.3, "news": 1.0, "web": 0.8, "social": 0.4, "lowtrust": 0.2}


@dataclass
class Evidence:
    source: Source
    stance: Stance
    cluster_id: int = -1


@dataclass
class Voice:
    """One independent voice: a syndication cluster, with its consensus stance."""

    id: int
    cluster: Cluster
    items: list[Evidence]
    stance: str
    weight: float
    rule: str

    @property
    def origin(self) -> Evidence:
        """Earliest member, skipping scraper sites whose dates cannot be trusted."""
        return next((e for e in self.items if e.source.kind != "lowtrust"), self.items[0])

    @property
    def representative(self) -> Evidence:
        """The member that best states this voice's stance (for dissent quotes)."""
        same = [e for e in self.items if e.stance.label == self.stance] or self.items
        return max(same, key=lambda e: e.stance.confidence)

    @property
    def outlets(self) -> list[str]:
        seen: list[str] = []
        for e in self.items:
            if e.source.outlet not in seen:
                seen.append(e.source.outlet)
        return seen


@dataclass
class EngineStat:
    engine: str
    queries: list[str]
    results: int = 0
    relevant: int = 0
    unique_relevant: int = 0
    errors: list[str] = field(default_factory=list)
    cached: bool = True


@dataclass
class Verdict:
    claim: str
    label: str
    score: float  # -1 (contradicted) .. +1 (supported)
    summary: str
    naive: Counter
    independent: Counter
    weighted: dict[str, float]
    voices: list[Voice]
    evidence: list[Evidence]  # every relevant source, ordered for the table
    earliest: Evidence | None
    earliest_support: Evidence | None
    earliest_contradiction: Evidence | None
    dissent: list[Voice]
    syndicated: list[Voice]  # voices made of >1 source
    engines: list[EngineStat]
    stance_method: str
    total_sources: int


def _vote(items: list[Evidence]) -> str:
    score: Counter = Counter()
    for e in items:
        score[e.stance.label] += max(e.stance.confidence, 0.05)
    # A cluster that both supports and partially supports is a supporting voice.
    if score[SUPPORTS] and score[SUPPORTS] >= score[CONTRADICTS]:
        return SUPPORTS if score[SUPPORTS] >= score[PARTIAL] * 0.5 else PARTIAL
    if score[CONTRADICTS] and score[CONTRADICTS] >= score[PARTIAL] * 0.5:
        return CONTRADICTS
    return PARTIAL


def _weight(items: list[Evidence]) -> float:
    best = 0.0
    for e in items:
        w = KIND_WEIGHT.get(e.source.kind, 0.8)
        if e.source.kind == "scholar" and (e.source.cited_by or 0) >= 50:
            w += 0.2
        best = max(best, w)
    return best


def _date_key(e: Evidence) -> datetime:
    return e.source.published  # type: ignore[return-value]


def build_voices(evidence: list[Evidence]) -> list[Voice]:
    clusters = cluster([e.source for e in evidence], [e.stance.label for e in evidence])
    voices: list[Voice] = []
    for cid, cl in enumerate(clusters):
        items = [evidence[k] for k in cl.members]
        for e in items:
            e.cluster_id = cid
        voices.append(Voice(cid, cl, items, _vote(items), _weight(items), cl.rule))
    # Separate clusters from the same non-social outlet are still one voice
    # for counting purposes (two Times of India pieces are not independent).
    by_dom: dict[str, Voice] = {}
    merged: list[Voice] = []
    for v in voices:
        dom = registrable_domain(v.origin.source.domain)
        kind = v.origin.source.kind
        if kind == "social" or kind == "scholar":
            merged.append(v)
            continue
        prev = by_dom.get(dom)
        if prev and prev.stance == v.stance:
            prev.items += v.items
            for e in v.items:
                e.cluster_id = prev.id
            if prev.rule == "single":
                prev.rule = "publisher"
            continue
        by_dom[dom] = v
        merged.append(v)
    for v in merged:
        v.items.sort(key=lambda e: (e.source.published is None,
                                    e.source.published.timestamp() if e.source.published else 0))
    return merged


def _label(score: float, n: int, ind: Counter, fc_contra: int, fc_support: int) -> str:
    if n < 2:
        return "Insufficient evidence"
    if fc_contra >= 2 and ind[CONTRADICTS] > ind[SUPPORTS] and score <= -0.2:
        return "False: debunked by fact-checkers"
    if score >= 0.5:
        return "Supported"
    if score >= 0.2:
        return "Leaning supported"
    if score <= -0.5:
        return "Contradicted"
    if score <= -0.2:
        return "Leaning contradicted"
    return "Contested / mixed evidence"


def score_claim(claim: str, sources: list[Source], stances: list[Stance], engines: list[EngineStat],
                stance_method: str) -> Verdict:
    evidence = [Evidence(s, st) for s, st in zip(sources, stances) if st.label != UNRELATED]
    voices = build_voices(evidence)

    naive = Counter(e.stance.label for e in evidence)
    independent = Counter(v.stance for v in voices)
    weighted = {lab: round(sum(v.weight for v in voices if v.stance == lab), 2)
                for lab in (SUPPORTS, CONTRADICTS, PARTIAL)}
    ws, wc, wp = weighted[SUPPORTS], weighted[CONTRADICTS], weighted[PARTIAL]
    score = (ws - wc) / (ws + wc + 0.5 * wp) if (ws + wc + wp) else 0.0

    fc_contra = sum(1 for v in voices if v.stance == CONTRADICTS
                    and any(e.source.is_factchecker for e in v.items))
    fc_support = sum(1 for v in voices if v.stance == SUPPORTS
                     and any(e.source.is_factchecker for e in v.items))
    label = _label(score, len(voices), independent, fc_contra, fc_support)

    # "Earliest trace": the first dated source that actually repeats the claim
    # (not a tangential paper that shares a few keywords). Scholar's year-only
    # dates are too coarse to win this race unless nothing else is dated.
    dated = [e for e in evidence if e.source.published]
    trace = [e for e in dated if e.stance.label in (SUPPORTS, PARTIAL) and e.stance.relevance >= 0.75]
    trace = [e for e in trace if e.source.kind != "lowtrust"] or trace
    precise = [e for e in trace if e.source.kind != "scholar"]
    earliest = min(precise or trace, key=_date_key, default=None) or min(dated, key=_date_key, default=None)
    earliest_support = min((e for e in dated if e.stance.label == SUPPORTS), key=_date_key, default=None)
    rebuttals = [e for e in dated if e.stance.label == CONTRADICTS]
    earliest_contra = min([e for e in rebuttals if e.stance.relevance >= 0.6] or rebuttals, key=_date_key, default=None)

    majority = SUPPORTS if ws >= wc else CONTRADICTS
    opposite = CONTRADICTS if majority == SUPPORTS else SUPPORTS
    dissent = sorted((v for v in voices if v.stance == opposite), key=lambda v: (-v.weight, v.id))[:4]
    syndicated = [v for v in voices if len(v.items) > 1]

    collapsed = len(evidence) - len(voices)
    summary = (
        f"{len(evidence)} relevant sources collapse to {len(voices)} independent voices "
        f"({collapsed} were copies/syndications). Independent: {independent[SUPPORTS]} support, "
        f"{independent[CONTRADICTS]} contradict, {independent[PARTIAL]} partial."
    )
    if fc_contra:
        summary += f" {fc_contra} fact-checker voice{'s' if fc_contra > 1 else ''} contradict{'s' if fc_contra == 1 else ''} it."

    # Table order: by voice (biggest syndication clusters first), then date.
    order = {v.id: i for i, v in enumerate(sorted(voices, key=lambda v: (-len(v.items), v.id)))}
    evidence.sort(key=lambda e: (order.get(e.cluster_id, 999),
                                 e.source.published.timestamp() if e.source.published else 9e12))

    return Verdict(
        claim=claim, label=label, score=round(score, 2), summary=summary, naive=naive,
        independent=independent, weighted=weighted, voices=voices, evidence=evidence,
        earliest=earliest, earliest_support=earliest_support, earliest_contradiction=earliest_contra,
        dissent=dissent, syndicated=sorted(syndicated, key=lambda v: -len(v.items)), engines=engines,
        stance_method=stance_method, total_sources=len(sources),
    )
