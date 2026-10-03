# TASK: Build the SerpApi India Hackathon submission (overnight autopilot run)

You are running unattended overnight (started 2026-10-03 ~22:30 IST). Vir is asleep and
will check in the morning of 2026-10-04. **Hard deadline: submission by 2026-10-05 23:59 IST.**
Nothing has been built yet. This run should take the project as far toward submittable
as possible.

## Read first (read-only, outside this workspace)
1. `~/Documents/Work/hackathon/WORKFLOW.md`: who does what. You are the builder.
2. `~/Documents/Work/hackathon/01-serpapi-india/BRIEF.md`: rules, judging criteria, and
   the chosen topic (§6 primary pick). Its day-by-day plan in §7 assumed 14 days.
   You have one night, so use the compressed plan below instead.

All code lives **here**, in `~/autopilot/serpapi-hackathon/`. Don't write to `~/Documents`.

## What to build
**The primary pick from BRIEF.md §6: a claim-triangulation / source-consensus engine.**
Track: Knowledge & Public Interest (news literacy). Python.

Input: a factual claim (text). Output: a verdict card that shows:
- **Consensus strength**: independent supporting vs. contradicting sources, after
  syndication is collapsed.
- **Earliest / primary source**: link and date.
- **Syndication clusters**: "these 12 articles are one wire story", shown explicitly.
- **Notable dissent / outliers.**
- Per-source table: engine, outlet, date, stance, one-line evidence snippet.

Pipeline:
1. **Query generation.** Make 2–4 query variants of the claim, including a neutral one and a
   "debunk/fact check" one.
2. **Multi-engine fan-out.** Use at least Google Search, Google News, and Google Scholar
   (add a date-restricted variant via `tbs`). Run them concurrently. Using several engines is
   the main thing that answers the "meaningful SerpApi usage" judging criterion, so make
   it visible in the output and README.
3. **Normalise + dedup** across engines (canonical URL, outlet domain, parsed date).
4. **Stance classification** per source: supports / contradicts / partial / unrelated.
5. **Syndication detection.** Group sources by title/snippet similarity plus close
   publication timestamps, so a cluster counts as one independent confirmation. This is
   the differentiator: get it working, test it, and explain it in the README.
6. **Verdict card.**

Interfaces: a CLI (`claimcheck "claim text"`) **and** a minimal web UI (FastAPI + a single
HTML page, or Streamlit, whichever is faster for you). The demo is: paste claim → verdict card.

## Constraints and decisions already made
- **SerpApi key:** read from `SERPAPI_API_KEY` in `./.env` (gitignored). Vir may not have
  added it before you start. **If it's missing, don't stop.** Build against recorded
  fixtures and finish everything else. Note this at the top of REPORT.md.
- **Credit budget is tiny** (Free Plan: 250 searches/month, verified 2026-10-03 with 250 left;
  the key is already in `.env`).
  **Cache every SerpApi response to disk from the first call** (`.cache/`, keyed by
  engine + params), and replay from the cache during development. If a key is present,
  keep live calls under **~40 for the whole night**. Check with the account API
  (`https://serpapi.com/account.json`) before and after you use the live API.
- **Fixtures:** commit a few cached real responses (if you have a key), or realistic
  hand-built ones (if you don't), under `fixtures/`, plus a `--offline` flag that runs
  the whole pipeline from them. Use 2–3 demo claims, ideally India-relevant forwarded
  claims, including one that is clearly syndicated wire copy and one that is false.
- **LLM for stance:** there's no LLM API key on this machine. Make stance classification
  pluggable:
  - **Default: works with no key.** Use a local NLI model (e.g. a small
    `cross-encoder/nli-*` or `MoritzLaurer/*-nli` via `transformers`) or, if that is too
    heavy, a heuristic. The project must run end-to-end for a judge without an LLM key.
  - **Optional:** an LLM backend (Anthropic / OpenAI / Gemini) that is used if
    `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GEMINI_API_KEY` is set. Write it and unit-test
    it with a mock. You can't test it live.
- Use `uv` for the env (`uv init`, `uv add`). Python 3.14 is the system Python. Pin
  `requires-python` to a version your deps support and let uv fetch it if needed.
- Keep the scope tight: no auth, no accounts, no DB beyond the disk cache.

## Compressed plan (in this order, commit after each step)
1. Scaffold: uv project, package layout, SerpApi client with disk cache, `.env` loading.
2. Fixtures + an end-to-end hardcoded run for one claim (offline).
3. Fan-out, normalisation, dedup.
4. Stance classification (local default + optional LLM).
5. Syndication clustering + verdict scoring. Write unit tests for this specifically.
6. CLI with a nicely formatted verdict card (`rich` is fine).
7. Web UI.
8. If a key is present: run the 2–3 demo claims live once, cache them, and commit the
   cached results as fixtures so the demo always works offline.
9. README: what it does, who benefits, **how SerpApi is used (engines, why each one)**,
   architecture diagram (mermaid), syndication explanation, setup (`uv sync`, `.env`,
   run commands, `--offline`), limitations.
10. Verify a **clean clone** works: `git clone . /tmp/cc-test && cd /tmp/cc-test && uv sync
    && <offline demo>`. Run the tests.
11. Submission materials in `submission/`:
    - `DESCRIPTION.md`: project description (purpose, who benefits, SerpApi integration).
    - `AI_DISCLOSURE.md`: honest AI-tools disclosure. Built with Claude Code (Anthropic),
      directed by Vir. Required by the rules, and it doesn't affect judging. Don't understate it.
    - `PRIOR_WORK.md`: none; built fresh 2026-10-03/04.
    - `VIDEO_SCRIPT.md`: under 3 minutes, with a shot list, timed narration, and the exact
      `wf-recorder` command to record on this Hyprland machine. Include the syndication
      explanation; the brief says it impresses judges.

## Not allowed tonight (the guard blocks these anyway)
- Creating the GitHub repo or pushing. **Put the exact `gh repo create` + push commands in
  `OUTBOX.md`.** Vir will run them. The repo must be public for submission.
- Signing up for anything, or filling in the submission form.
- No Claude co-author trailers or "Generated with" footers in commits.

## When done, or when you can't go further
Write `REPORT.md` and keep it updated as you go, in case the session stops early on a
usage limit. Put a **"Vir's morning checklist"** at the top, covering exactly what Vir
must do, in order:
add the key to `.env` (if missing) → run X to cache live results → run the OUTBOX commands to publish
the repo → record the video → fill in the form and submit. Then list what was built, how it was verified
(commands + test output), assumptions, and what is unfinished.
