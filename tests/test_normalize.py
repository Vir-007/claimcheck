from datetime import datetime, timezone

from claimcheck.normalize import canonical_url, classify_kind, dedup, detect_wire, domain_of, parse_date

from helpers import src

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def test_canonical_url_strips_tracking_amp_www():
    a = canonical_url("https://www.thehindu.com/news/story/amp/?utm_source=wa&utm_medium=share")
    b = canonical_url("http://m.thehindu.com/news/story")
    assert a == b == "https://thehindu.com/news/story"


def test_canonical_url_keeps_meaningful_query():
    assert canonical_url("https://eric.ed.gov/?id=ED383611") == "https://eric.ed.gov/?id=ED383611"


def test_domain_of():
    assert domain_of("https://english.factcrescendo.com/x") == "factcrescendo.com"


def test_parse_date_formats():
    assert parse_date("2026-10-02T06:08:00Z") == datetime(2026, 10, 2, 6, 8, tzinfo=timezone.utc)
    assert parse_date("10/02/2026, 06:08 PM, +0000 UTC") == datetime(2026, 10, 2, 18, 8, tzinfo=timezone.utc)
    assert parse_date("18 Dec 2020") == datetime(2020, 12, 18, tzinfo=timezone.utc)
    assert parse_date("Dec 18, 2020") == datetime(2020, 12, 18, tzinfo=timezone.utc)
    assert parse_date("3 days ago", NOW) == datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    assert parse_date("nonsense") is None
    assert parse_date(None) is None


def test_classify_kind():
    assert classify_kind("https://www.altnews.in/x", "google") == "factcheck"
    assert classify_kind("https://timesofindia.indiatimes.com/times-fact-check/news/x", "google_news") == "factcheck"
    assert classify_kind("https://timesofindia.indiatimes.com/india/x", "google_news") == "news"
    assert classify_kind("https://www.facebook.com/groups/1", "google") == "social"
    assert classify_kind("https://x.org/paper", "google_scholar") == "scholar"
    assert classify_kind("https://somewhere.in/a", "google_news", "BOOM Fact Check") == "factcheck"


def test_detect_wire():
    assert detect_wire("New Delhi, Oct 2 (PTI) The government said") == "PTI"
    assert detect_wire("Press Trust of India") == "PTI"
    assert detect_wire("ANI reported") == "ANI"
    assert detect_wire("Pakistan's PTI party") == "PTI"  # known false positive; only lowers a threshold
    assert detect_wire("an ordinary sentence") is None


def test_dedup_merges_engines_and_keeps_best_fields():
    a = src("Story", "https://www.site.com/a/?utm_source=x", None, snippet="short", engine="google")
    b = src("Story", "https://site.com/a", "2026-10-02T06:00:00", snippet="a much longer snippet", engine="google_news")
    out = dedup([a, b])
    assert len(out) == 1
    assert out[0].engines == ["google", "google_news"]
    assert out[0].snippet == "a much longer snippet"
    assert out[0].published is not None
