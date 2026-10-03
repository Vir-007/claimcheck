from datetime import datetime, timezone

from claimcheck.normalize import Hit, Source, canonical_url, classify_kind, domain_of, detect_wire


def src(title: str, url: str, when: str | None = None, snippet: str = "", engine: str = "google_news",
        outlet: str | None = None, kind: str | None = None) -> Source:
    published = datetime.fromisoformat(when).replace(tzinfo=timezone.utc) if when else None
    return Source(
        title=title, url=url, canonical_url=canonical_url(url), domain=domain_of(url),
        outlet=outlet or domain_of(url), snippet=snippet, published=published,
        date_text=(when + "Z") if when and "T" in when else when,
        hits=[Hit(engine, "keywords", f"{engine}:keywords", 1)],
        kind=kind or classify_kind(url, engine, outlet or ""), wire=detect_wire(snippet, outlet),
    )
