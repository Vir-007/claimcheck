import json

import pytest

from claimcheck import stance as st
from claimcheck.stance import (CONTRADICTS, PARTIAL, SUPPORTS, UNRELATED, HeuristicBackend, LLMBackend,
                               build_llm_prompt, make_backend, numeric_conflict, parse_llm_json, relevance)

from helpers import src

ANTHEM = "UNESCO declared Jana Gana Mana the best national anthem in the world"
CRASH = "Seven people were killed when a truck hit a pickup van in Amravati, Maharashtra"


def h(claim, title, snippet="", url="https://example.com/a"):
    return HeuristicBackend().one(claim, src(title, url, "2020-01-01", snippet=snippet))


def test_relevance_uses_number_words_and_stems():
    assert relevance(CRASH, "7 killed as truck hits pickup van in Amravati, Maharashtra") > 0.7


def test_numeric_conflict():
    assert numeric_conflict(CRASH, "Five killed, 21 injured as truck hits pickup van")
    assert not numeric_conflict(CRASH, "Seven killed, 19 injured")
    assert not numeric_conflict(CRASH, "Truck hits pickup van in 2026")  # years ignored


def test_heuristic_labels():
    assert h(ANTHEM, "FAKE ALERT: No, UNESCO did not declare Indian national anthem best in the world").label == CONTRADICTS
    assert h(ANTHEM, "Jana Gana Mana declared best national anthem in the world by UNESCO").label == SUPPORTS
    assert h(ANTHEM, "Did UNESCO declare Jana Gana Mana the best national anthem in the world?").label == PARTIAL
    assert h(ANTHEM, "Petrol prices rise again in Delhi").label == UNRELATED
    assert h(CRASH, "Five killed, 21 injured as truck hits pickup van in Maharashtra's Amravati").label == PARTIAL


def test_true_or_false_is_not_a_debunk():
    r = h(ANTHEM, "UNESCO declares Indian National Anthem best in the world: True or false?")
    assert r.label == PARTIAL


def test_parse_llm_json_tolerates_prose_and_bad_rows():
    text = 'Sure! ```json\n{"results": [{"id": 0, "label": "supports", "confidence": 0.9, "evidence": "x"},' \
           '{"id": 5, "label": "supports", "confidence": 1, "evidence": "out of range"},' \
           '{"id": 1, "label": "bogus", "confidence": 1, "evidence": ""}]}\n```'
    out = parse_llm_json(text, 2)
    assert out[0].label == SUPPORTS and out[0].confidence == 0.9
    assert out[1] is None


def test_llm_prompt_numbers_sources():
    p = build_llm_prompt(ANTHEM, [src("A", "https://a.com"), src("B", "https://b.com")])
    assert "[0]" in p and "[1]" in p and ANTHEM in p


def test_llm_backend_with_mock_transport():
    sources = [
        src("UNESCO declared Jana Gana Mana best national anthem in the world", "https://a.com/1"),
        src("Fact check: UNESCO did not declare Jana Gana Mana best national anthem", "https://b.com/2"),
        src("Cricket score today", "https://c.com/3"),  # filtered as unrelated before the LLM
    ]
    seen = {}

    def transport(system, user):
        seen["user"] = user
        return json.dumps({"results": [
            {"id": 0, "label": "supports", "confidence": 0.8, "evidence": "declared best"},
            {"id": 1, "label": "contradicts", "confidence": 0.95, "evidence": "did not declare"},
        ]})

    out = LLMBackend(transport, HeuristicBackend(), "mock").classify(ANTHEM, sources)
    assert [o.label for o in out] == [SUPPORTS, CONTRADICTS, UNRELATED]
    assert out[1].method == "llm:mock"
    assert "Cricket" not in seen["user"]


def test_llm_backend_falls_back_on_error():
    def boom(system, user):
        raise RuntimeError("network down")

    sources = [src("FAKE: UNESCO did not declare Jana Gana Mana best national anthem in the world", "https://a.com")]
    out = LLMBackend(boom, HeuristicBackend(), "mock").classify(ANTHEM, sources)
    assert out[0].label == CONTRADICTS and out[0].method == "heuristic"


def test_factory_picks_llm_when_key_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(st, "NLIBackend", lambda: HeuristicBackend())
    b = make_backend("auto")
    assert b.name == "llm:openai"


def test_factory_without_keys_is_local(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    def broken():
        raise OSError("no model")

    monkeypatch.setattr(st, "NLIBackend", broken)
    assert make_backend("auto").name == "heuristic"
    with pytest.raises(OSError):
        make_backend("nli")


def test_anthropic_transport_request_shape(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    captured = {}

    class Block:
        type = "text"
        text = '{"results": []}'

    class Resp:
        stop_reason = "end_turn"
        content = [Block()]

    class Messages:
        def create(self, **kw):
            captured.update(kw)
            return Resp()

    class Client:
        messages = Messages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda: Client())
    call = st.anthropic_transport()
    assert call("sys", "user") == '{"results": []}'
    assert captured["model"] == "claude-opus-5"
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["system"] == "sys"
