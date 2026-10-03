# Demo video script (target 2:40, hard limit 3:00)

Production quality does not affect judging. One take of the screen with your voice is enough.
About 390 words of narration at a calm pace is about 2:40.

## Before recording (2 minutes of setup)

```bash
cd ~/autopilot/serpapi-hackathon        # or your clone of github.com/Vir-007/claimcheck
uv sync
uv run claimcheck --offline --demo amravati >/dev/null   # warms the NLI model so nothing lags on camera
uv run claimcheck-web &                                   # http://127.0.0.1:8000
```

* Browser: open `http://127.0.0.1:8000`, zoom to 110-125% so text reads on video, close other tabs.
* Terminal: a second workspace with a large font (Ctrl + = a few times), `clear`.
* Leave **offline unticked**: the demo claims are cached, so every click replays recorded SerpApi
  responses instantly and spends no credits. The engine table will say "cache"; that's fine and
  honest.
* Mute notifications (Omarchy: toggle Do Not Disturb).

## Recording command (this machine: Hyprland, monitor eDP-1, 1920×1080)

`gpu-screen-recorder` is already installed, so use it:

```bash
mkdir -p ~/Videos
gpu-screen-recorder -w eDP-1 -f 30 -a default_input -o ~/Videos/claimcheck-demo.mp4
# ... record ... then press Ctrl+C in that terminal to stop
```

`wf-recorder` is **not installed** (installing it needs sudo; see NEEDS_SUDO.md). If you install it,
the equivalent is:

```bash
wf-recorder -o eDP-1 --audio=alsa_input.pci-0000_03_00.6.analog-stereo -f ~/Videos/claimcheck-demo.mp4
# Ctrl+C to stop. To record just one window/area instead:  wf-recorder -g "$(slurp)" --audio -f ~/Videos/claimcheck-demo.mp4
```

Check length and trim the dead second at each end (ffmpeg is installed):

```bash
ffprobe -v error -show_entries format=duration -of csv=p=0 ~/Videos/claimcheck-demo.mp4
ffmpeg -ss 1.0 -to 170 -i ~/Videos/claimcheck-demo.mp4 -c copy ~/Videos/claimcheck-final.mp4   # adjust -to
```

Start the recorder, then switch to the browser workspace (Super+number) before speaking.

## Shot list and narration

| Time | Shot (what's on screen) | Narration (read this) |
|---|---|---|
| 0:00-0:15 | Browser, claimcheck home page, empty box. | "Every Indian family group gets forwards. The usual check is: search it, and see how many sites say the same thing. That check is broken, and this tool fixes it." |
| 0:15-0:35 | Click the **amravati** demo. Card loads. Point the cursor at "Naive count 8 / 8" then "Independent voices 5 / 5". | "Here's a real accident from the second of October. Seven killed when a truck hit a pickup van in Amravati. Sixteen relevant articles. But claimcheck doesn't count articles. It counts independent voices." |
| 0:35-1:05 | Scroll to **Syndication clusters**. Hover the first cluster's outlet chips, then the timeline dots. | "These five outlets (The Hindu, Tribune, Times of India, Pioneer, DNA) are one PTI wire story: near-identical headlines, published within thirteen hours. That's one confirmation, not five. And this second cluster is the *earlier* wire version, 'five killed, twenty-one injured'. You can watch the toll get updated." |
| 1:05-1:25 | Scroll to **SerpApi engines used**. | "This only works because of SerpApi. Every claim fans out into six concurrent searches: Google web, Google with a past-month date filter, Google News, and Google Scholar. News gives the exact timestamps that wire detection needs. Web finds the forward itself. Scholar finds the research. Almost every source here was found by only one engine." |
| 1:25-1:55 | Click the **anthem** demo. Pause on the red "False: debunked by fact-checkers" banner, then scroll to the Facebook copy-paste clusters. | "Now a classic forward: UNESCO declared Jana Gana Mana the best anthem in the world. False. Three independent fact-checkers contradict it. And the 'support'? Look: these Facebook posts are word-for-word the same message, down to 'Just few minutes ago'. One forward, pasted again and again, collapsed into one voice." |
| 1:55-2:15 | Scroll to **Origin** (earliest trace 2010, first rebuttal) and the sources table with fact-checker badges. | "It also shows when the claim first appears in results, here 2010, and the first rebuttal, with links, so you can read the primary sources yourself." |
| 2:15-2:30 | Switch to terminal. Type and run: `uv run claimcheck --offline --demo papaya --rows 8` | "Same engine in the terminal. Papaya leaf for dengue: Scholar trials support a platelet effect, fact-checkers reject the 'cure' framing. The verdict is honest: contested." |
| 2:30-2:45 | Stay on the terminal output (verdict panel visible). | "Stance runs on a small local model, no AI key needed, and every SerpApi response is cached, so the whole demo replays offline. claimcheck: count who actually confirmed it, not how many times it was copied." |

Stop the recorder (Ctrl+C).

## If you'd rather not narrate

Record the screen silently following the shot list. Then either add on-screen captions using
the narration column, or generate a voice-over with any TTS and mux it in:

```bash
ffmpeg -i ~/Videos/claimcheck-demo.mp4 -i voiceover.wav -map 0:v -map 1:a -c:v copy -shortest ~/Videos/claimcheck-final.mp4
```

## Upload

Upload to YouTube as **Unlisted** (or Google Drive with "anyone with the link") and paste the
link into the submission form and the README's top section.
