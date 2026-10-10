# AI tools disclosure

**Tool used:** Claude Code (Anthropic), running the Claude Opus 5.5 model.

**How it was used:** this project was built almost entirely by Claude Code, working from a
written brief and directed by Vir Sharma. Concretely, Claude Code:

* wrote all of the source code (SerpApi client and cache, query planning, fan-out,
  normalisation, stance classification, syndication clustering, verdict scoring, CLI, web UI);
* wrote all of the tests;
* chose the demo claims, ran the live SerpApi searches and recorded them as fixtures;
* tuned the stance rules and clustering thresholds against the recorded data;
* wrote the README, this disclosure, the project description and the demo video script;
* captured the demo footage and assembled the video. The voice-over is AI text-to-speech
  (Kokoro, open-source, run locally) reading the script.

**What Vir did:** chose the hackathon and the topic (claim triangulation with syndication
detection), wrote the brief and the constraints the build followed (engines, caching and credit
budget, local-first stance model, scope), created the SerpApi account and key, reviewed the
result, reviewed and uploaded the demo video, and submits the entry. Vir is responsible for the
submission as a whole.

**Inside the product:** by default, stance classification uses a small open-source NLI model
(`Xenova/nli-deberta-v3-xsmall`, an ONNX export of a DeBERTa-v3 cross-encoder trained on SNLI/MNLI)
running locally. No LLM is needed to run the project. An optional backend calls Anthropic, OpenAI
or Gemini if the user supplies an API key; it is unit-tested with mocks only and was not run live.
