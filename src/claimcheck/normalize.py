"""Turn heterogeneous SerpApi engine responses into one ``Source`` shape, then
merge the same document found by several engines/queries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .fanout import TaskResult

# Wire agencies whose copy is republished verbatim by many Indian outlets.
WIRE_AGENCIES = {
    "PTI": r"\bPTI\b|Press Trust of India",
    "ANI": r"\bANI\b|Asian News International",
    "IANS": r"\bIANS\b",
    "UNI": r"\bUNI\b|United News of India",
    "Reuters": r"\bReuters\b",
    "AFP": r"\bAFP\b|Agence France",
    "AP": r"\(AP\)|Associated Press",
}

FACT_CHECKERS = {
    "altnews.in", "boomlive.in", "factly.in", "factchecker.in", "vishvasnews.com", "newschecker.in",
    "thequint.com/news/webqoof", "factcrescendo.com", "newsmeter.in", "newsmobile.in", "thip.media",
    "pib.gov.in", "snopes.com", "politifact.com", "fullfact.org", "factcheck.afp.com", "verafiles.org",
    "reuters.com/fact-check", "indiatoday.in/fact-check", "timesofindia.indiatimes.com/times-fact-check",
    "thenewsminute.com/news/tnm-fact-check", "logicallyfacts.com", "factcheck.org", "leadstories.com",
}

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|ref$|ref_|cmpid|ito$|from$|amp$|_ga|igshid|hl$|s$)")


@dataclass
class Hit:
    """One appearance of a document in one engine's results."""

    engine: str
    variant: str
    task_label: str
    position: int


@dataclass
class Source:
    title: str
    url: str
    canonical_url: str
    domain: str
    outlet: str
    snippet: str
    published: datetime | None
    date_text: str | None
    hits: list[Hit] = field(default_factory=list)
    kind: str = "web"  # web | news | scholar | social | factcheck | lowtrust
    cited_by: int | None = None
    wire: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def engines(self) -> list[str]:
        return sorted({h.engine for h in self.hits})

    @property
    def best_position(self) -> int:
        return min((h.position for h in self.hits), default=99)

    @property
    def is_factchecker(self) -> bool:
        return self.kind == "factcheck"

    @property
    def text(self) -> str:
        return f"{self.title}. {self.snippet}".strip(". ")


# --- URL / outlet -------------------------------------------------------------
def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    for prefix in ("www.", "m.", "amp.", "mobile."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = re.sub(r"/(amp|amp\.html)/?$", "/", parts.path)
    path = re.sub(r"\.amp(\.html)?$", "", path)
    path = path.rstrip("/") or "/"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k.lower())))
    return urlunsplit(("https", host, path, query, ""))


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    for prefix in ("www.", "m.", "amp.", "mobile.", "english."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


_SOCIAL = ("facebook.com", "instagram.com", "x.com", "twitter.com", "youtube.com", "sharechat.com",
           "brainly.in", "quora.com", "reddit.com", "linkedin.com", "tiktok.com", "threads.net")


# Throwaway TLDs favoured by scraper/spam sites that republish news with wrong dates.
_LOW_TRUST_TLDS = {"pics", "xyz", "top", "click", "pro", "buzz", "icu", "cfd", "sbs", "lol", "monster", "quest",
                   "rest", "bond", "shop", "online", "site", "fun", "live"}


def is_low_trust(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    labels = host.split(".")
    if labels and labels[-1] in _LOW_TRUST_TLDS:
        return True
    # Random-looking subdomain, e.g. 81uyqn2p.example.pics
    return len(labels) >= 3 and bool(re.fullmatch(r"(?=.*\d)[a-z0-9]{7,}", labels[0]))


def is_factcheck_url(url: str) -> bool:
    canon = canonical_url(url).removeprefix("https://")
    host = canon.split("/")[0]
    for fc in FACT_CHECKERS:
        if "/" in fc:
            if canon.startswith(fc):
                return True
        elif host == fc or host.endswith("." + fc):
            return True
    return False


def classify_kind(url: str, engine: str, outlet: str = "") -> str:
    if is_factcheck_url(url) or re.search(r"(?i)fact ?check|factly", outlet):
        return "factcheck"
    if engine == "google_scholar":
        return "scholar"
    dom = domain_of(url)
    if any(dom == s or dom.endswith("." + s) for s in _SOCIAL):
        return "social"
    if is_low_trust(url):
        return "lowtrust"
    if engine == "google_news":
        return "news"
    return "web"


def detect_wire(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    for name, pat in WIRE_AGENCIES.items():
        if re.search(pat, blob):
            return name
    return None


# --- dates --------------------------------------------------------------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_REL = re.compile(r"(\d+)\s+(minute|min|hour|day|week|month|year)s?\s+ago", re.I)


def parse_date(text: str | None, now: datetime | None = None) -> datetime | None:
    """Parse the date formats SerpApi engines emit.

    ISO ("2026-10-02T06:08:00Z"), Google News ("10/02/2026, 06:08 AM, +0000 UTC"),
    Google organic ("18 Dec 2020", "Dec 18, 2020"), relative ("3 days ago"), and a
    bare year (Scholar's "..., 2019 - Elsevier").
    """
    if not text:
        return None
    text = text.strip()
    now = now or datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    m = re.match(r"(\d{2})/(\d{2})/(\d{4}), (\d{1,2}):(\d{2}) (AM|PM)", text)
    if m:
        mo, d, y, hh, mm, ap = m.groups()
        h = int(hh) % 12 + (12 if ap == "PM" else 0)
        return datetime(int(y), int(mo), int(d), h, int(mm), tzinfo=timezone.utc)
    m = _REL.search(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"minute": 1 / 1440, "min": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7,
                "month": 30, "year": 365}[unit]
        return now - timedelta(days=n * days)
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3})[a-z]*,? (\d{4})", text)
    if m and m.group(2).lower() in _MONTHS:
        return datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)
    m = re.search(r"([A-Za-z]{3})[a-z]* (\d{1,2}), (\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc)
    return None


def _scholar_year(summary: str) -> datetime | None:
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", summary or "")
    return datetime(int(m.group(1)), 1, 1, tzinfo=timezone.utc) if m else None


# --- per-engine extraction ----------------------------------------------------
def _from_google(item: dict[str, Any], r: TaskResult, now: datetime) -> Source | None:
    link = item.get("link")
    if not link:
        return None
    date_text = item.get("date") or _date_in(item.get("displayed_link"))
    url = link
    return Source(
        title=item.get("title", ""),
        url=url,
        canonical_url=canonical_url(url),
        domain=domain_of(url),
        outlet=item.get("source") or domain_of(url),
        snippet=item.get("snippet", ""),
        published=parse_date(date_text, now),
        date_text=date_text,
        hits=[Hit(r.task.engine, r.task.variant.name, r.task.label, item.get("position", 99))],
        kind=classify_kind(url, "google"),
        wire=detect_wire(item.get("snippet")),
    )


def _date_in(displayed: str | None) -> str | None:
    if displayed and _REL.search(displayed):
        return _REL.search(displayed).group(0)
    return None


def _from_news(item: dict[str, Any], r: TaskResult, now: datetime) -> Source | None:
    link = item.get("link")
    if not link:
        return None
    src = item.get("source") or {}
    date_text = item.get("iso_date") or item.get("date")
    return Source(
        title=item.get("title", ""),
        url=link,
        canonical_url=canonical_url(link),
        domain=domain_of(link),
        outlet=src.get("name") or domain_of(link),
        snippet=item.get("snippet", ""),
        published=parse_date(date_text, now),
        date_text=date_text,
        hits=[Hit("google_news", r.task.variant.name, r.task.label, item.get("position", 99))],
        kind=classify_kind(link, "google_news", src.get("name") or ""),
        wire=detect_wire(src.get("name"), item.get("snippet")),
        extra={"authors": src.get("authors", [])},
    )


def _from_scholar(item: dict[str, Any], r: TaskResult, now: datetime) -> Source | None:
    link = item.get("link")
    if not link:
        return None
    info = item.get("publication_info") or {}
    summary = info.get("summary", "")
    venue = summary.split(" - ")[1] if summary.count(" - ") >= 1 else ""
    return Source(
        title=item.get("title", ""),
        url=link,
        canonical_url=canonical_url(link),
        domain=domain_of(link),
        outlet=re.sub(r",?\s*(19|20)\d\d$", "", venue).strip() or domain_of(link),
        snippet=(item.get("snippet") or "").replace("…", "").strip(),
        published=_scholar_year(summary),
        date_text=(m.group(0) if (m := re.search(r"\b(19|20)\d\d\b", summary)) else None),
        hits=[Hit("google_scholar", r.task.variant.name, r.task.label, item.get("position", 99))],
        kind="scholar",
        cited_by=((item.get("inline_links") or {}).get("cited_by") or {}).get("total"),
        extra={"authors": summary.split(" - ")[0] if summary else ""},
    )


def extract(results: list[TaskResult], now: datetime | None = None) -> list[Source]:
    """Flatten every engine response into Sources (no dedup yet)."""
    now = now or datetime.now(timezone.utc)
    out: list[Source] = []
    for r in results:
        if not r.response:
            continue
        # Relative dates ("3 days ago") are relative to when SerpApi ran the search,
        # not to when a cached fixture is replayed.
        now = _created_at(r.response) or now
        if r.task.engine == "google":
            items = list(r.response.get("organic_results", []))
            for story in r.response.get("top_stories", []) or []:
                items.append({**story, "snippet": story.get("snippet", "")})
            out += [s for it in items if (s := _from_google(it, r, now))]
        elif r.task.engine == "google_news":
            for it in r.response.get("news_results", []):
                # Google News sometimes nests a topic cluster under "stories".
                for sub in [it, *it.get("stories", [])]:
                    if (s := _from_news(sub, r, now)):
                        out.append(s)
        elif r.task.engine == "google_scholar":
            out += [s for it in r.response.get("organic_results", []) if (s := _from_scholar(it, r, now))]
    return out


def _created_at(response: dict[str, Any]) -> datetime | None:
    raw = (response.get("search_metadata") or {}).get("created_at")
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc) if raw else None
    except ValueError:
        return None


def dedup(sources: list[Source]) -> list[Source]:
    """Merge the same document (canonical URL) found by several engines/queries.

    The merged source keeps every ``Hit`` so the card can show which engines
    found it, and takes the richest snippet and the most precise date.
    """
    merged: dict[str, Source] = {}
    for s in sources:
        cur = merged.get(s.canonical_url)
        if cur is None:
            merged[s.canonical_url] = s
            continue
        cur.hits.extend(s.hits)
        if len(s.snippet) > len(cur.snippet):
            cur.snippet = s.snippet
        if s.published and (cur.published is None or _precision(s) > _precision(cur)):
            cur.published, cur.date_text = s.published, s.date_text
        if cur.outlet == cur.domain and s.outlet != s.domain:
            cur.outlet = s.outlet
        cur.wire = cur.wire or s.wire
        cur.cited_by = cur.cited_by or s.cited_by
    return sorted(merged.values(), key=lambda s: (s.best_position, s.title))


def _precision(s: Source) -> int:
    if s.date_text and ("T" in s.date_text or "UTC" in s.date_text):
        return 3
    if s.date_text and "ago" in s.date_text:
        return 1
    return 2 if s.date_text and not re.fullmatch(r"\d{4}", s.date_text or "") else 0
