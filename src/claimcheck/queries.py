"""Query generation and the multi-engine search plan.

Generation is deterministic (template-based, no LLM) on purpose: the same claim
must always produce the same SerpApi requests so cached responses and committed
fixtures replay exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

STOPWORDS = set(
    """a an the and or but if of to in on at by for with from as is are was were be been being
    that this these those it its into than then so such very can could will would should may might
    has have had do does did not no yes all any some more most other over under about after before
    there their they them he she his her we our you your i me my who whom which what when where why
    how also just only even still per via says said say claim claims claimed reportedly forwarded
    viral message whatsapp please share""".split()
)

# Phrases that WhatsApp forwards wrap around the actual claim.
_FORWARD_NOISE = re.compile(
    r"(?i)\b(forwarded( many times)?|share (this|it) with everyone|breaking( news)?|must read|"
    r"please share|viral)\b[:!.,]*"
)


@dataclass(frozen=True)
class QueryVariant:
    name: str  # neutral | keywords | factcheck | recent
    q: str
    purpose: str


@dataclass(frozen=True)
class SearchTask:
    engine: str
    variant: QueryVariant
    params: dict[str, Any] = field(hash=False)
    why: str = ""

    @property
    def label(self) -> str:
        suffix = f" [{self.params['tbs']}]" if "tbs" in self.params else ""
        return f"{self.engine}:{self.variant.name}{suffix}"


def clean_claim(claim: str) -> str:
    claim = _FORWARD_NOISE.sub(" ", claim)
    claim = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", " ", claim)  # emoji
    claim = re.sub(r"\s+", " ", claim).strip(" .!?\"'")
    return claim


def keywords(claim: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[\w'’-]+", clean_claim(claim))
    out: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        t = tok.strip("'’-")
        low = t.lower()
        if low in STOPWORDS or len(t) < 2 or low in seen:
            continue
        seen.add(low)
        out.append(t)
    return out[:limit]


def make_variants(claim: str) -> list[QueryVariant]:
    neutral = clean_claim(claim)
    kw = " ".join(keywords(claim))
    return [
        QueryVariant("neutral", neutral, "the claim as a user would type it"),
        QueryVariant("keywords", kw, "stop-words stripped; News and Scholar match short queries better"),
        QueryVariant("factcheck", f"{kw} fact check", "surfaces fact-checkers and debunks"),
    ]


def plan(claim: str, gl: str = "in", hl: str = "en") -> list[SearchTask]:
    """The fan-out: which engine gets which variant, and why.

    Six SerpApi calls per claim across three engines plus a date-restricted run.
    """
    v = {qv.name: qv for qv in make_variants(claim)}
    base = {"gl": gl, "hl": hl}
    return [
        SearchTask("google", v["neutral"], {"engine": "google", "q": v["neutral"].q, **base},
                   "general web: blogs, government pages, Wikipedia, first-hand sources"),
        SearchTask("google", v["factcheck"], {"engine": "google", "q": v["factcheck"].q, **base},
                   "fact-checkers (Alt News, BOOM, PIB, Factly...) rank for 'fact check' queries"),
        SearchTask("google", v["keywords"], {"engine": "google", "q": v["keywords"].q, "tbs": "qdr:m", **base},
                   "date-restricted (past month): is the claim circulating right now?"),
        SearchTask("google_news", v["keywords"], {"engine": "google_news", "q": v["keywords"].q, **base},
                   "news outlets with exact timestamps; the raw material for syndication detection"),
        SearchTask("google_news", v["factcheck"], {"engine": "google_news", "q": v["factcheck"].q, **base},
                   "news-indexed fact-check desks and corrections"),
        SearchTask("google_scholar", v["keywords"], {"engine": "google_scholar", "q": v["keywords"].q, "hl": hl},
                   "peer-reviewed evidence; a population news and web search never reach"),
    ]
