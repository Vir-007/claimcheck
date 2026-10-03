"""Per-source stance classification: supports / contradicts / partial / unrelated.

Three pluggable backends, all behind ``classify(claim, sources)``:

* ``heuristic`` - keyword relevance + debunk/hedge cue phrases + numeric
  agreement. Zero dependencies, always available, used as the fallback.
* ``nli``       - a local DeBERTa-v3 NLI cross-encoder (ONNX, ~87 MB, CPU),
  downloaded from Hugging Face on first use. Combined with the heuristic's
  relevance gate and cue phrases. Default when the model can be loaded.
* ``llm``       - one batched call to Anthropic / OpenAI / Gemini, used only
  when the matching API key is set. Falls back to the local backend on error.

``auto`` picks llm > nli > heuristic based on what is available.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .normalize import Source
from .queries import keywords

SUPPORTS, CONTRADICTS, PARTIAL, UNRELATED = "supports", "contradicts", "partial", "unrelated"
LABELS = (SUPPORTS, CONTRADICTS, PARTIAL, UNRELATED)


@dataclass
class Stance:
    label: str
    confidence: float
    evidence: str
    method: str
    relevance: float = 0.0


class Backend(Protocol):
    name: str

    def classify(self, claim: str, sources: list[Source]) -> list[Stance]: ...


# --- shared text features -----------------------------------------------------
_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "dozen": "12",
    "fifteen": "15", "twenty": "20", "hundred": "100",
}

DEBUNK = re.compile(
    r"(?i)\b(fake|(?<!true or )false|falsely|hoax|misleading|debunk\w*|myths?|baseless|fabricated|rumou?rs?|"
    r"misinformation|disinformation|not true|untrue|no evidence|no proof|no scientific|"
    r"did ?n[o']t|does ?n[o']t|do ?n[o']t|is ?n[o']t|was ?n[o']t|were ?n[o']t|has ?n[o']t|never|"
    r"cannot|can ?not|no,|not declared|not recogni[sz]ed|doesn't rank|does not rank|"
    r"fake alert|fact ?check:? (no|false|fake)|scam|doctored|morphed|edited|old video|satire|"
    r"right,? foolish|sorry guys|fell for|falls for)\b"
)
HEDGE = re.compile(
    r"(?i)\b(may|might|could|some studies|limited evidence|more research|needs context|missing context|"
    r"partly|partially|half[- ]true|mixed|divided|no miracle|not a cure|not cure|experts weigh|"
    r"what experts say|what doctors say|here'?s what|can (it|they|papaya)\b.*\?|really\b|myth or fact|"
    r"true or false|helps,? but|preliminary|in some|modest)\b"
)

CONFIRM = re.compile(r"(?i)\b(true|correct|yes,|real|genuine|confirmed|accurate)\b")


def norm_tokens(text: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower().replace("’", "'"))
    return [_NUMBER_WORDS.get(t, t) for t in toks]


def _stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s", "e"):
        if len(t) > (3 if suf == "s" else 4) and t.endswith(suf):
            return t[: -len(suf)]
    return t


def relevance(claim: str, text: str) -> float:
    """Fraction of the claim's content words present in the text (stemmed)."""
    kw = {_stem(t) for t in norm_tokens(" ".join(keywords(claim, limit=20)))}
    if not kw:
        return 0.0
    have = {_stem(t) for t in norm_tokens(text)}
    return len(kw & have) / len(kw)


def numbers(text: str) -> set[str]:
    return {t for t in norm_tokens(text) if t.isdigit() and len(t) < 5 and not (1900 <= int(t) <= 2100)}


def numeric_conflict(claim: str, text: str) -> bool:
    """Claim says 7, source says 5 (and never 7): a partial match at best."""
    c, s = numbers(claim), numbers(text)
    return bool(c and s and not (c & s))


def best_sentence(claim: str, text: str, limit: int = 160) -> str:
    # Split on real sentence ends only, not on the ".." / "…" that Google uses
    # to elide text mid-sentence.
    parts = [p.strip() for p in re.split(r"(?<![.…])(?<=[.!?])\s+(?=[A-Z\"“‘'])|\s+[·|]\s+", text)
             if len(p.strip()) > 25]
    if not parts:
        return text[:limit]
    best = max(parts, key=lambda p: relevance(claim, p) + (0.3 if DEBUNK.search(p) else 0))
    return best if len(best) <= limit else best[: limit - 1].rsplit(" ", 1)[0] + "…"


def is_question(s: Source) -> bool:
    """Headlines that ask rather than assert ("Did UNESCO...?", "...? Find out here!")."""
    title = s.title.strip()
    return title.rstrip(".… ").endswith("?") or bool(
        re.match(r"(?i)(fact ?check:\s*)?(did|does|is|was|were|can|could|will|has|have|do)\b", title)
    ) or bool(re.search(r"(?i)\?\s+(find out|watch|read|know|check)\b", s.snippet))


def checks_claim(s: Source) -> bool:
    """A fact-checker, or any page framing itself as a fact check, quotes the
    claim it examines; that quote must not count as support."""
    return s.is_factchecker or bool(re.search(r"(?i)fact[- ]?check|factly|#factcheck", f"{s.outlet} {s.text}"))


def mostly_non_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) < 0.6


def _negated_in_claim(claim: str) -> bool:
    return bool(re.search(r"(?i)\b(not|no|never|n't|doesn't|didn't|cannot)\b", claim))


# --- heuristic ----------------------------------------------------------------
class HeuristicBackend:
    name = "heuristic"

    def __init__(self, min_relevance: float = 0.34):
        self.min_relevance = min_relevance

    def one(self, claim: str, s: Source) -> Stance:
        text = s.text
        rel = relevance(claim, text)
        ev = best_sentence(claim, text)
        if rel < self.min_relevance:
            return Stance(UNRELATED, round(1 - rel, 2), ev, self.name, rel)
        debunk = bool(DEBUNK.search(text)) and not _negated_in_claim(claim)
        if debunk:
            return Stance(CONTRADICTS, round(0.55 + 0.4 * rel, 2), ev, self.name, rel)
        if HEDGE.search(text) or is_question(s) or numeric_conflict(claim, text):
            return Stance(PARTIAL, round(0.4 + 0.3 * rel, 2), ev, self.name, rel)
        if rel >= 0.6 and not (checks_claim(s) and not CONFIRM.search(s.title)):
            return Stance(SUPPORTS, round(0.45 + 0.5 * rel, 2), ev, self.name, rel)
        return Stance(PARTIAL, round(0.35 + 0.3 * rel, 2), ev, self.name, rel)

    def classify(self, claim: str, sources: list[Source]) -> list[Stance]:
        return [self.one(claim, s) for s in sources]


# --- local NLI ----------------------------------------------------------------
DEFAULT_NLI_MODEL = os.environ.get("CLAIMCHECK_NLI_MODEL", "Xenova/nli-deberta-v3-xsmall")


class NLIBackend:
    """DeBERTa-v3 NLI cross-encoder via onnxruntime (no torch needed).

    premise = source title + snippet, hypothesis = the claim. The NLI verdict is
    gated by keyword relevance (an off-topic page cannot "contradict" a claim)
    and nudged by the heuristic's debunk cues, which NLI models trained on
    MNLI handle poorly in headline-ese ("FAKE ALERT: ...").
    """

    name = "nli"

    def __init__(self, model_id: str = DEFAULT_NLI_MODEL, min_relevance: float = 0.34):
        import numpy as np  # noqa: F401  (fail fast if missing)
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        def fetch(name: str) -> str:
            # Use the local HF cache when present, so repeat runs (and --offline
            # runs) never touch the network; download on first use otherwise.
            try:
                return hf_hub_download(model_id, name, local_files_only=True)
            except Exception:
                if os.environ.get("HF_HUB_OFFLINE") == "1":
                    raise
                return hf_hub_download(model_id, name)

        tok_path = fetch("tokenizer.json")
        cfg_path = fetch("config.json")
        onnx_path = fetch("onnx/model_quantized.onnx")
        self.tok = Tokenizer.from_file(tok_path)
        self.tok.enable_truncation(max_length=256)
        self.tok.enable_padding()
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(onnx_path, opts, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        id2label = json.load(open(cfg_path))["id2label"]
        self.idx = {v.lower(): int(k) for k, v in id2label.items()}
        self.min_relevance = min_relevance
        self.heur = HeuristicBackend(min_relevance)

    def probs(self, pairs: list[tuple[str, str]], batch: int = 16) -> list[dict[str, float]]:
        import numpy as np

        out: list[dict[str, float]] = []
        for i in range(0, len(pairs), batch):
            enc = self.tok.encode_batch(pairs[i : i + batch])
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            }
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.array([e.type_ids for e in enc], dtype=np.int64)
            logits = self.sess.run(None, feed)[0]
            ex = np.exp(logits - logits.max(axis=1, keepdims=True))
            p = ex / ex.sum(axis=1, keepdims=True)
            out += [{k: float(row[j]) for k, j in self.idx.items()} for row in p]
        return out

    def classify(self, claim: str, sources: list[Source]) -> list[Stance]:
        heur = self.heur.classify(claim, sources)
        todo = [i for i, h in enumerate(heur) if h.relevance >= self.min_relevance]
        probs = self.probs([(sources[i].text[:600], claim) for i in todo]) if todo else []
        out = list(heur)
        for i, p in zip(todo, probs):
            h, src = heur[i], sources[i]
            if mostly_non_latin(src.text):
                continue  # English NLI model; keep the heuristic verdict
            ent, con, neu = p.get("entailment", 0), p.get("contradiction", 0), p.get("neutral", 0)
            question = is_question(src)
            if h.label == CONTRADICTS and con >= ent:
                label, conf = CONTRADICTS, max(con, h.confidence)
            elif con >= 0.5 and h.label != CONTRADICTS:
                # NLI-only contradiction, no debunk language. In news this is
                # usually a *different* event ("5 of family killed in car-truck
                # collision") or a differing detail, not a rebuttal.
                if numeric_conflict(claim, src.text) and h.relevance >= 0.5:
                    label, conf = PARTIAL, con * 0.6
                elif h.relevance >= 0.75:
                    label, conf = CONTRADICTS, con * 0.8
                else:
                    label, conf = UNRELATED, con * 0.5
            elif h.label == CONTRADICTS:
                label, conf = CONTRADICTS, h.confidence * 0.85
            elif ent >= 0.5 and not question and not numeric_conflict(claim, src.text) and not (
                    checks_claim(src) and not CONFIRM.search(src.title)):
                # Fact-checkers quote the claim they are checking, so their
                # headlines only count as support when they explicitly confirm it.
                label, conf = SUPPORTS, ent
            elif ent >= 0.5 or question or h.label == PARTIAL and HEDGE.search(src.text) \
                    or numeric_conflict(claim, src.text):
                label, conf = PARTIAL, max(ent, neu) * 0.75
            elif h.relevance >= 0.6 and h.label == SUPPORTS:
                # On-topic and consistent, but less specific than the claim
                # ("7 killed in Maharashtra" vs "... in Amravati").
                label, conf = PARTIAL, 0.4
            else:
                # Topical but takes no position (NLI neutral): it is not evidence.
                label, conf = UNRELATED, neu
            out[i] = Stance(label, round(conf, 2), h.evidence, self.name, h.relevance)
        return out


# --- LLM ----------------------------------------------------------------------
LLM_SYSTEM = (
    "You are a careful fact-checking assistant. For each numbered search result, decide its stance "
    "toward the CLAIM using only the title and snippet shown. Labels: supports (asserts the claim is "
    "true), contradicts (asserts it is false, debunks it, or reports facts incompatible with it), "
    "partial (supports part of it, hedges, or reports materially different details such as a "
    "different number), unrelated (does not address the claim). Quote the shortest phrase from the "
    "snippet that justifies the label as evidence."
)

LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string", "enum": list(LABELS)},
                    "confidence": {"type": "number"},
                    "evidence": {"type": "string"},
                },
                "required": ["id", "label", "confidence", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def build_llm_prompt(claim: str, sources: list[Source]) -> str:
    lines = [f"CLAIM: {claim}", "", "RESULTS:"]
    for i, s in enumerate(sources):
        lines.append(f"[{i}] {s.outlet} | {s.title} | {s.snippet[:400]}")
    lines.append("")
    lines.append('Respond with JSON: {"results": [{"id": <n>, "label": ..., "confidence": 0-1, "evidence": ...}]}'
                 " with one entry per result.")
    return "\n".join(lines)


def parse_llm_json(text: str, n: int) -> list[Stance | None]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in LLM output")
    data = json.loads(m.group(0))
    out: list[Stance | None] = [None] * n
    for r in data.get("results", []):
        i = int(r.get("id", -1))
        label = str(r.get("label", "")).lower()
        if 0 <= i < n and label in LABELS:
            conf = max(0.0, min(1.0, float(r.get("confidence", 0.6))))
            out[i] = Stance(label, round(conf, 2), str(r.get("evidence", ""))[:200], "llm")
    return out


Transport = Callable[[str, str], str]  # (system, user) -> model text


def anthropic_transport(model: str | None = None) -> Transport:
    import anthropic

    client = anthropic.Anthropic()
    model = model or os.environ.get("CLAIMCHECK_LLM_MODEL", "claude-opus-5")

    def call(system: str, user: str) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": LLM_SCHEMA}},
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("model declined the request")
        return "".join(b.text for b in resp.content if b.type == "text")

    return call


def openai_transport(model: str | None = None) -> Transport:
    import httpx

    model = model or os.environ.get("CLAIMCHECK_LLM_MODEL", "gpt-4.1-mini")

    def call(system: str, user: str) -> str:
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": model, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    return call


def gemini_transport(model: str | None = None) -> Transport:
    import httpx

    model = model or os.environ.get("CLAIMCHECK_LLM_MODEL", "gemini-2.5-flash")

    def call(system: str, user: str) -> str:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": user}]}],
                  "generationConfig": {"responseMimeType": "application/json"}},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    return call


def llm_provider_from_env() -> str | None:
    for env, name in (("ANTHROPIC_API_KEY", "anthropic"), ("OPENAI_API_KEY", "openai"), ("GEMINI_API_KEY", "gemini")):
        if os.environ.get(env):
            return name
    return None


class LLMBackend:
    name = "llm"

    def __init__(self, transport: Transport, fallback: Backend, provider: str = "llm", chunk: int = 40):
        self.transport = transport
        self.fallback = fallback
        self.provider = provider
        self.chunk = chunk
        self.name = f"llm:{provider}"

    def classify(self, claim: str, sources: list[Source]) -> list[Stance]:
        local = self.fallback.classify(claim, sources)
        out = list(local)
        # Only spend tokens on sources the cheap relevance gate kept.
        idx = [i for i, s in enumerate(local) if s.label != UNRELATED]
        for start in range(0, len(idx), self.chunk):
            part = idx[start : start + self.chunk]
            try:
                text = self.transport(LLM_SYSTEM, build_llm_prompt(claim, [sources[i] for i in part]))
                parsed = parse_llm_json(text, len(part))
            except Exception:
                continue  # keep the local verdicts for this chunk
            for j, st in zip(part, parsed):
                if st is not None:
                    st.method = self.name
                    st.relevance = local[j].relevance
                    st.evidence = st.evidence or local[j].evidence
                    out[j] = st
        return out


# --- factory ------------------------------------------------------------------
_TRANSPORTS = {"anthropic": anthropic_transport, "openai": openai_transport, "gemini": gemini_transport}


def make_backend(kind: str = "auto", log: Callable[[str], None] = lambda m: None) -> Backend:
    kind = kind.lower()
    if kind == "heuristic":
        return HeuristicBackend()

    local: Backend
    if kind in ("auto", "nli", "llm"):
        try:
            local = NLIBackend()
        except Exception as e:
            if kind == "nli":
                raise
            log(f"NLI model unavailable ({type(e).__name__}); using heuristic stance")
            local = HeuristicBackend()
    else:
        raise ValueError(f"unknown stance backend {kind!r}")
    if kind == "nli":
        return local

    provider = llm_provider_from_env()
    if provider:
        try:
            return LLMBackend(_TRANSPORTS[provider](), local, provider)
        except Exception as e:  # e.g. anthropic package not installed
            log(f"LLM backend {provider} unavailable ({type(e).__name__}: {e}); using {local.name}")
    elif kind == "llm":
        log("no ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY set; using local stance")
    return local
