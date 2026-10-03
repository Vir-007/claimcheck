# claimcheck: who actually confirmed this?

**Track:** Knowledge & Public Interest (news literacy)
**Repo:** https://github.com/Vir-007/claimcheck

## One line

Paste a factual claim and get a verdict card that counts **independent** confirmations, not
articles. Wire copy, syndicated rewrites and copy-pasted WhatsApp forwards are collapsed
before anything is counted.

## Purpose

When an Indian family WhatsApp group receives a forward, the usual check is to search it and
see how many sites say the same thing. That check fails in a specific way. Forty outlets
"confirming" a story are often one PTI wire story reprinted forty times, and hundreds of
"people posting it" are often one message pasted over and over since 2012. Counting results
measures how far something was copied, not how many independent parties verified it.

claimcheck takes a claim and returns:

* **Consensus strength**, counted two ways: a naive count of every article, and a count of
  independent voices after syndication is collapsed, weighted by source type
  (fact-checker > peer-reviewed > news > web > social > scraper).
* **Earliest trace and first rebuttal**, with links and dates.
* **Syndication clusters**, e.g. "these 5 articles are one wire story, published over 13 hours",
  with the rule that grouped them.
* **Notable dissent**: independent voices that disagree with the majority.
* A **per-source table**: engine, outlet, date, stance, confidence, cluster, evidence snippet.

It runs as a CLI (`claimcheck "claim"`) and a single-page web UI.

## Who benefits

* People handed a forward who want more than "it's on lots of sites".
* Fact-check and news desks deciding whether a story is widely reported or only widely copied.
* Students, teachers and researchers checking whether a health claim has real literature
  behind it. The papaya-leaf/dengue demo shows supportive trials alongside fact-checkers
  pushing back on the "cure" framing.

## How SerpApi is integrated

SerpApi is the substrate the tool is built on, not a step at the end. Each claim produces six
concurrent SerpApi calls across three engines plus a date-restricted variant:

| Engine | Query | Contribution |
|---|---|---|
| `google` | claim as typed | the forward itself on social sites, blogs, official pages |
| `google` | keywords + "fact check" | Indian fact-checkers (Alt News, BOOM, Factly, Vishvas, PIB...) |
| `google` + `tbs=qdr:m` | keywords, past month | whether the claim is circulating right now |
| `google_news` | keywords | outlets with exact timestamps, needed for wire-copy detection |
| `google_news` | keywords + "fact check" | news-indexed fact-check desks |
| `google_scholar` | keywords | peer-reviewed evidence for health and science claims |

The engines return different populations. In the demos almost every relevant source was found
by exactly one engine, and the card shows this per engine. Results are normalised (canonical
URL, outlet, six date formats), merged across engines, classified for stance, and then
clustered:

* sources are linked when headlines/snippets nearly match **and** were published within
  72 hours (wire copy), or share a publisher network, or are verbatim copies at any date
  (forwards);
* clustering uses **complete linkage**, so one source must match every member and chains
  can't snowball;
* sources with opposite stances are never merged.

Stance runs locally by default: a small DeBERTa NLI model via ONNX, so no API key is needed.
Anthropic, OpenAI or Gemini are used if a key is present.

Every SerpApi response is cached to disk from the first call, with a hard per-process cap on
live calls. Real responses for four demo claims are committed, so judges can run the full demo
offline. The whole build used 25 searches.

## Demo results (real recorded SerpApi data)

* **"UNESCO declared Jana Gana Mana the best national anthem"**: False, debunked by fact-checkers.
  The Facebook "confirmations" collapse into copy-paste clusters of the same forward ("Just few
  minutes ago").
* **"Seven killed when a truck hit a pickup van in Amravati"** (2 Oct 2026): 16 articles collapse
  to 10 voices. One PTI cluster of 5 outlets, plus a cluster carrying the earlier "five killed"
  wire version.
* **"Papaya leaf extract increases platelet count in dengue"**: contested / mixed evidence.
  Scholar trials support it; fact-checkers dispute the cure framing.
* **"Gujarat became the 2nd state with a UCC"** (2 Oct 2026, recorded live): supported; a scraper
  site with a wrong date is flagged as low-trust and never chosen as the origin.
