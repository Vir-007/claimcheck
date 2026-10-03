"""End-to-end over the committed fixtures (recorded live SerpApi responses).

Uses the heuristic stance backend so the suite needs neither network nor the
NLI model; one test exercises NLI when its model is already cached locally.
"""

import json

import pytest

from claimcheck.cli import main
from claimcheck.pipeline import check, demo_claims, verdict_dict
from claimcheck.serp import SerpClient

DEMOS = {d["id"]: d["claim"] for d in demo_claims()}


def run(claim, stance="heuristic"):
    return check(claim, client=SerpClient(offline=True), stance=stance)


def test_three_demo_claims_have_fixtures():
    assert {"anthem", "amravati", "papaya", "ucc"} <= set(DEMOS)
    for claim in DEMOS.values():
        v, info = run(claim)
        assert info.live_calls == 0 and info.cache_hits == 6
        assert {e.engine for e in v.engines} == {"google", "google_news", "google_scholar"}
        assert not any(e.errors for e in v.engines)
        assert v.total_sources > 30


def test_anthem_is_false():
    v, _ = run(DEMOS["anthem"])
    # Even the zero-dependency heuristic backend leans the right way: the
    # supporting voices are low-weight social reposts, the contradicting ones
    # fact-checkers. (The NLI backend gets the full "False" label; see below.)
    assert v.score < 0
    assert v.weighted["contradicts"] > v.weighted["supports"]


def test_amravati_wire_story_collapses():
    v, _ = run(DEMOS["amravati"])
    assert v.score > 0
    biggest = v.syndicated[0]
    assert len(biggest.items) >= 4
    assert biggest.rule == "wire"
    assert sum(v.naive.values()) > len(v.voices)


def test_json_serialisable():
    v, info = run(DEMOS["papaya"])
    d = verdict_dict(v, info)
    json.dumps(d)
    assert d["evidence"] and d["engines"]


def test_cli_offline_demo(capsys):
    assert main(["--offline", "--demo", "amravati", "--stance", "heuristic", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claim"] == DEMOS["amravati"]


def test_cli_offline_unknown_claim_explains(capsys):
    assert main(["--offline", "--stance", "heuristic", "a claim with no fixtures at all"]) == 1


def test_nli_end_to_end_if_model_cached():
    pytest.importorskip("onnxruntime")
    from claimcheck.stance import NLIBackend
    try:
        NLIBackend()
    except Exception:
        pytest.skip("NLI model not available offline")
    v, _ = run(DEMOS["anthem"], stance="nli")
    assert v.label.startswith("False")
    v, _ = run(DEMOS["amravati"], stance="nli")
    assert v.label in ("Supported", "Leaning supported")
