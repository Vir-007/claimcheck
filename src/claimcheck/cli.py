"""`claimcheck "claim text"` - print a verdict card in the terminal."""

from __future__ import annotations

import argparse
import json
import sys

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .pipeline import check, demo_claims, load_env, verdict_dict
from .stance import CONTRADICTS, PARTIAL, SUPPORTS
from .verdict import Evidence, Verdict

STANCE_STYLE = {SUPPORTS: "green", CONTRADICTS: "red", PARTIAL: "yellow"}
STANCE_ICON = {SUPPORTS: "✔ supports", CONTRADICTS: "✘ contradicts", PARTIAL: "◐ partial"}
ENGINE_SHORT = {"google": "Web", "google_news": "News", "google_scholar": "Scholar"}
RULE_TEXT = {
    "wire": "wire copy: near-identical text published within 72h",
    "publisher": "same publisher network",
    "copy-paste": "copy-pasted text (forward / repost)",
    "undated": "near-identical text",
}


def label_style(label: str) -> str:
    if label.startswith(("False", "Contradicted", "Leaning contradicted")):
        return "bold white on red"
    if label.startswith(("Supported", "Leaning supported")):
        return "bold white on green"
    return "bold black on yellow"


def _date(e: Evidence | None) -> str:
    if not e or not e.source.published:
        return "undated"
    d = e.source.published
    if e.source.date_text and e.source.date_text.strip().isdigit():
        return d.strftime("%Y")
    if "T" in (e.source.date_text or "") or "UTC" in (e.source.date_text or ""):
        return d.strftime("%d %b %Y %H:%M UTC")
    return d.strftime("%d %b %Y")


def _src_line(e: Evidence) -> Text:
    t = Text()
    t.append(f"{e.source.outlet}", style="bold")
    t.append(f"  {_date(e)}\n", style="dim")
    t.append(e.source.title[:110] + "\n")
    t.append(e.source.url, style="blue underline")
    return t


def render(v: Verdict, console: Console, max_rows: int = 30) -> None:
    head = Text()
    head.append(f" {v.label} ", style=label_style(v.label))
    head.append(f"   consensus score {v.score:+.2f}  (−1 contradicted … +1 supported)\n\n", style="dim")
    head.append(v.summary)

    # Naive vs independent: the headline number of the whole project.
    counts = Table(box=box.SIMPLE_HEAVY, show_edge=False, pad_edge=False)
    counts.add_column("")
    for lab in (SUPPORTS, CONTRADICTS, PARTIAL):
        counts.add_column(STANCE_ICON[lab], style=STANCE_STYLE[lab], justify="right")
    counts.add_row("Naive count (every article)", *[str(v.naive.get(l, 0)) for l in (SUPPORTS, CONTRADICTS, PARTIAL)])
    counts.add_row("[bold]Independent voices[/] (syndication collapsed)",
                   *[f"[bold]{v.independent.get(l, 0)}[/]" for l in (SUPPORTS, CONTRADICTS, PARTIAL)])
    counts.add_row("Credibility-weighted", *[f"{v.weighted.get(l, 0):.1f}" for l in (SUPPORTS, CONTRADICTS, PARTIAL)])

    origin = Table.grid(padding=(0, 2))
    origin.add_column(style="bold cyan", no_wrap=True)
    origin.add_column()
    if v.earliest:
        origin.add_row("Earliest trace", _src_line(v.earliest))
    if v.earliest_contradiction:
        origin.add_row("First rebuttal", _src_line(v.earliest_contradiction))

    console.print(Panel(Group(head, Text(""), counts, Text(""), origin),
                        title=f"[bold]claimcheck[/]: “{v.claim}”", border_style="cyan", padding=(1, 2)))

    if v.syndicated:
        tbl = Table(title="Syndication clusters: each counts once", box=box.ROUNDED, title_justify="left",
                    title_style="bold magenta", expand=True)
        tbl.add_column("#", justify="right", width=3)
        tbl.add_column("Copies", justify="right", width=6)
        tbl.add_column("Stance", width=13)
        tbl.add_column("Why grouped")
        tbl.add_column("Outlets")
        tbl.add_column("Origin (earliest)")
        for i, voice in enumerate(v.syndicated, 1):
            why = RULE_TEXT.get(voice.rule, voice.rule)
            if voice.cluster.wire:
                why += f"; byline {voice.cluster.wire}"
            ds = [e.source.published for e in voice.items if e.source.published]
            if len(ds) > 1 and voice.rule == "wire":
                why += f"; spread {(max(ds) - min(ds)).total_seconds() / 3600:.0f}h"
            tbl.add_row(str(i), str(len(voice.items)),
                        Text(STANCE_ICON[voice.stance], style=STANCE_STYLE[voice.stance]), why,
                        ", ".join(voice.outlets[:6]) + (f" +{len(voice.outlets) - 6}" if len(voice.outlets) > 6 else ""),
                        f"{voice.origin.source.outlet} · {_date(voice.origin)}")
        console.print(tbl)

    if v.dissent:
        d = Table(title="Notable dissent (against the weighted majority)", box=box.ROUNDED, title_justify="left",
                  title_style="bold yellow", expand=True)
        d.add_column("Stance", width=13)
        d.add_column("Source")
        d.add_column("Evidence")
        for voice in v.dissent:
            e = voice.representative
            d.add_row(Text(STANCE_ICON[voice.stance], style=STANCE_STYLE[voice.stance]),
                      f"{e.source.outlet} ({e.source.kind}, {_date(e)})", e.stance.evidence)
        console.print(d)

    t = Table(title=f"Sources ({len(v.evidence)} relevant of {v.total_sources} found)", box=box.SIMPLE,
              title_justify="left", title_style="bold", expand=True)
    t.add_column("Engine", no_wrap=True)
    t.add_column("Outlet")
    t.add_column("Date", no_wrap=True)
    t.add_column("Stance", no_wrap=True)
    t.add_column("Cl", justify="right")
    t.add_column("Evidence snippet", ratio=3)
    cluster_sizes = {voice.id: len(voice.items) for voice in v.voices}
    for e in v.evidence[:max_rows]:
        engines = "+".join(ENGINE_SHORT.get(x, x) for x in e.source.engines)
        cl = str(e.cluster_id) if cluster_sizes.get(e.cluster_id, 1) > 1 else ""
        badge = " ✓FC" if e.source.is_factchecker else ""
        t.add_row(engines, e.source.outlet[:28] + badge, _date(e).replace(" UTC", ""),
                  Text(f"{STANCE_ICON[e.stance.label]} {e.stance.confidence:.2f}", style=STANCE_STYLE[e.stance.label]),
                  cl, e.stance.evidence)
    if len(v.evidence) > max_rows:
        t.caption = f"… {len(v.evidence) - max_rows} more (use --rows N or --json)"
    console.print(t)

    eng = Table(title="SerpApi engines used", box=box.SIMPLE, title_justify="left", title_style="bold")
    eng.add_column("Engine")
    eng.add_column("Queries")
    eng.add_column("Results", justify="right")
    eng.add_column("Relevant", justify="right")
    eng.add_column("Found only here", justify="right")
    for s in v.engines:
        eng.add_row(s.engine, ", ".join(s.queries), str(s.results), str(s.relevant), str(s.unique_relevant),
                    style="red" if s.errors else None)
    console.print(eng)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="claimcheck", description="Triangulate a factual claim across SerpApi engines.")
    p.add_argument("claim", nargs="?", help="the claim to check (quote it)")
    p.add_argument("--offline", action="store_true", help="replay cached/fixture responses only; no network")
    p.add_argument("--demo", metavar="ID", help="run a bundled demo claim (see --list-demos)")
    p.add_argument("--list-demos", action="store_true")
    p.add_argument("--stance", default="auto", choices=["auto", "nli", "heuristic", "llm"],
                   help="stance backend (default auto: LLM if a key is set, else local NLI, else heuristic)")
    p.add_argument("--json", action="store_true", help="print the verdict as JSON")
    p.add_argument("--rows", type=int, default=30, help="max rows in the source table")
    p.add_argument("--account", action="store_true", help="show SerpApi credits left (free call)")
    args = p.parse_args(argv)

    load_env()
    console = Console(stderr=False)
    err = Console(stderr=True)

    if args.account:
        from .serp import SerpClient
        acct = SerpClient.from_env().account()
        console.print({k: acct.get(k) for k in ("plan_name", "searches_per_month", "total_searches_left", "this_month_usage")})
        return 0

    demos = demo_claims()
    if args.list_demos:
        for d in demos:
            console.print(f"[bold]{d['id']}[/]: {d['claim']}\n  [dim]{d['why']}[/]")
        return 0
    claim = args.claim
    if args.demo:
        match = [d for d in demos if d["id"] == args.demo]
        if not match:
            err.print(f"unknown demo {args.demo!r}; try --list-demos")
            return 2
        claim = match[0]["claim"]
    if not claim:
        p.print_help()
        return 2

    from .serp import SerpClient
    client = SerpClient.from_env(offline=args.offline)
    if client.offline and not args.offline:
        err.print("[yellow]SERPAPI_API_KEY not set: running offline from cached fixtures.[/]")
    with err.status("fanning out across SerpApi engines…" if not client.offline else "replaying cached SerpApi responses…"):
        verdict, info = check(claim, client=client, stance=args.stance, log=lambda m: err.print(f"[dim]{m}[/]"))
    if verdict.total_sources == 0 and client.offline:
        err.print("[red]No cached responses for this claim.[/] Offline mode only has the demo claims:")
        for d in demos:
            err.print(f"  --demo {d['id']}   {d['claim']}")
        return 1

    if args.json:
        print(json.dumps(verdict_dict(verdict, info), indent=2, ensure_ascii=False))
    else:
        render(verdict, console, args.rows)
        console.print(f"[dim]stance: {verdict.stance_method} · {info.seconds}s · live SerpApi calls: "
                      f"{info.live_calls} · cache hits: {info.cache_hits}{' · offline' if info.offline else ''}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
