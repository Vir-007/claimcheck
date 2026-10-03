import json

import pytest

from claimcheck.serp import BudgetExceeded, CacheMiss, SerpClient, cache_key


def test_cache_key_ignores_api_key_and_order():
    a = cache_key({"engine": "google", "q": "x", "api_key": "secret"})
    b = cache_key({"q": "x", "engine": "google"})
    assert a == b and a.startswith("google-")


def test_offline_miss_raises(tmp_path):
    c = SerpClient(offline=True, cache_dir=tmp_path, fixture_dir=None)
    with pytest.raises(CacheMiss):
        c.search({"engine": "google", "q": "nothing"})


def test_cache_hit_and_fixture_fallback(tmp_path):
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    params = {"engine": "google_news", "q": "hello"}
    (fixtures / f"{cache_key(params)}.json").write_text(json.dumps({"response": {"news_results": [1]}}))
    c = SerpClient(offline=True, cache_dir=tmp_path / "cache", fixture_dir=fixtures)
    assert c.search(params) == {"news_results": [1]}
    assert c.cache_hits == 1


def test_live_call_is_cached_and_scrubbed(tmp_path, monkeypatch):
    calls = []

    class Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"search_metadata": {"id": "1", "json_endpoint": "https://serpapi.com/x?api_key=SECRET"},
                    "search_parameters": {"q": "x", "api_key": "SECRET"}, "organic_results": [{"link": "a"}]}

    def fake_get(url, params, timeout):
        calls.append(params)
        return Resp()

    monkeypatch.setattr("claimcheck.serp.httpx.get", fake_get)
    c = SerpClient(api_key="SECRET", cache_dir=tmp_path, fixture_dir=None, max_live_calls=1)
    params = {"engine": "google", "q": "x"}
    c.search(params)
    c.search(params)  # second call served from disk
    assert len(calls) == 1
    on_disk = next(tmp_path.glob("*.json")).read_text()
    assert "SECRET" not in on_disk
    with pytest.raises(BudgetExceeded):
        c.search({"engine": "google", "q": "different"})
