---
name: ue-video-capture
description: Records videos of Unreal Engine levels and demo scenes on macOS — with game audio, UMG/Slate UI and live camera moves — at an exact resolution and length, ready for YouTube, Fab/Marketplace listings or trailers. Use this whenever the user wants to record, capture, film or screen-record a UE map, demo, gameplay or showcase ("запиши видео сцены", "сними ролик с демо-карты", "record 30 seconds of this level with sound", "make a trailer clip of the demo map"), and whenever a UE recording went wrong — frames missing the UMG/Slate HUD, a -DUMPMOVIE capture with no sound, hotkeys dead in a launched game while the mouse still works, HUD at the wrong scale in fullscreen, desktop showing in the corners of a window capture, clipped or overly quiet audio. macOS only.
---

# Unreal Engine video capture (macOS)

Everything here was measured on real hardware (UE 5.8, macOS 26). Nearly every rule exists because
the obvious approach produced a wrong video **while every log, flag and exit code reported
success**. So the working habit this skill teaches is: when you are about to trust something,
look at a frame instead.

Scripts are in `scripts/` next to this file. Run them as `python3 <this skill's absolute directory>/scripts/<name>.py …`,
one command per call; they need only the standard library, ffmpeg and the Xcode command line tools.
The fixed form matters for permission rules — see "Ask first" below.

## First decide the capture mode

| The clip needs… | Mode |
|---|---|
| sound, UMG or Slate UI on screen, or a person moving the camera | **Real-time**: `record.py` → `finalize.py` |
| none of that — silent, HUD drawn only on the Canvas, perfectly even timing, or macOS permissions unavailable | **Frame dump**: `dump.py` |

This comes first because the wrong choice fails invisibly. `-DUMPMOVIE` never contains widgets
added to the viewport: the engine composites UI into a screenshot only when it is *not* dumping
(`GameViewportClient.cpp`, `if (!GIsDumpingMovie && ...) bShowUI = true`). A demo whose graphs live
in a UMG/Slate panel records as the scene with an empty corner, and nothing reports a problem. A HUD
drawn in `AHUD::DrawHUD` on the Canvas *is* captured. If you don't know how the project draws its
UI, grep for `AddViewportWidgetContent` / `AddToViewport` versus `DrawHUD` — or just use real-time.

## Ask what to record: resolution and duration

These two shape the whole take, so don't default them silently — a wrong guess means redoing the
capture, and for a frame dump, minutes of rendering thrown away. Ask before running `launch` or
`dump.py`:

- **Resolution.** Offer a short list rather than an open question: **1920×1080** (1080p — the
  default, and the only geometry verified on real hardware so far), **2560×1440** (1440p),
  **3840×2160** (4K — a frame dump writes roughly 4× the PNG data per second, and a real-time take
  needs a Mac fast enough to render and encode it live), or a custom size if the destination calls
  for one (a specific Fab/Marketplace listing, for example). Passed as `--res WxH` to `launch` /
  `dump.py`.
- **Duration.** How many usable seconds after "go" (real-time) or after the ready marker (dump).
  There's no good silent default — a showcase clip is commonly 15–30 s, a fuller walkthrough up to
  a minute or more. Passed as `--duration` to `take` / `dump.py`.

Ask both in one message, together with the permissions bundle below if that's also needed right now
— one short list of questions beats several one-line ones.

## Ask first: what a recording touches outside the project

A recording reaches well past the project. It asks macOS for privacy grants, may install an audio
driver and restart the sound system, changes where all of the Mac's sound goes, talks out loud and
covers the screen with a game window. None of it is destructive, but each step surprises someone who
didn't expect it, and most need the person's own hands — a password, a click in System Settings. So
be the one who brings it up: before the first step of each kind, say in a sentence or two what will
happen and ask; when something is missing, offer to walk them through it rather than just reporting
it. Bundle the questions — one short list at the start beats a question before every command — and
skip the ones that don't apply (a silent dump needs almost none of this).

| Step | What the person should hear first | Offer |
|---|---|---|
| Claude Code approving the scripts | Once the game is up it covers the terminal. If Claude Code then asks to approve `record.py take`, the prompt waits unseen behind the game while the person waits for a countdown that never comes. | Allow rules for the scripts (below), unless this session already runs without prompts. |
| First `preflight.py` on a new Mac | Its screen probe can make macOS show a dialog asking to allow screen recording. | Mention it before running. |
| Screen & System Audio Recording | A privacy grant for the terminal app. macOS then offers to quit and reopen that app, which ends this Claude Code session. | Walk through `references/setup.md` §3; suggest "Later" and restarting when nothing else is running. |
| `brew install blackhole-2ch` | Installs a system audio driver; asks for the administrator password. | Suggest `! brew install blackhole-2ch` so it runs in this session. |
| `sudo killall coreaudiod` | All sound on the Mac cuts out for about a second — noticeable in a call or with music playing. | Ask if now is fine; suggest `! sudo killall coreaudiod`. |
| Multi-Output Device as the output | All of the Mac's sound goes through it; the volume keys stop working until it's switched back. | Walk through setup.md §2; offer to switch back afterwards (§4). |
| `preflight.py --audio-test` | Says "testing one two three" through the speakers. | Ask first — headphones, a meeting, an open office. |
| `record.py launch`, `dump.py` | A game window opens in the middle of the screen and takes it over until it closes. A notification banner over the window would end up in the video. | Ask when they're ready; suggest Do Not Disturb. |
| `dump.py` finds an earlier dump's frames | They're in the project's `Saved/Screenshots` and would be counted as new; the script stops with exit code 4 rather than delete them. | Ask; re-run with `--clear-old-frames` only on a yes. |
| `record.py take` | Records the whole display, not only the game; the crop happens later. | Brief them (step 3) and wait for their go-ahead. |
| After the clip is approved | `raw.mov` holds the uncropped full screen, about 200 MB per 30 s take. | Offer to delete it. |

### Allowing the scripts in Claude Code

Bash permission rules match the command text as written, so a rule only helps if it has exactly the
prefix you run the scripts with — the absolute form above, with no `cd … &&` or pipe around it.
Offer rules of that shape, one per script, with this skill's real directory:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 /Users/<user>/.claude/skills/ue-video-capture/scripts/preflight.py *)",
      "Bash(python3 /Users/<user>/.claude/skills/ue-video-capture/scripts/record.py *)",
      "Bash(python3 /Users/<user>/.claude/skills/ue-video-capture/scripts/finalize.py *)",
      "Bash(python3 /Users/<user>/.claude/skills/ue-video-capture/scripts/dump.py *)"
    ]
  }
}
```

On a yes, merge them into `permissions.allow` in `~/.claude/settings.json` — read the file first and
keep everything already there — or let the person add them through `/permissions`. What makes a
standing approval reasonable is what the scripts do: launch the game, run ffmpeg and `say`, and
write under `Saved/VideoCaptures`. It is still their decision. Without the rules, warn them in the
briefing that an approval prompt for the take will appear in the terminal *behind* the game: switch
to it with Cmd+Tab, approve, then click inside the game window — which also gives the game its
keyboard focus back.

## Real-time capture

### 1. Preflight

```
python3 <skill dir>/scripts/preflight.py
```

It lists blocking items and names the `references/setup.md` section for each. Everything missing
needs the person — a `sudo` command, clicks in Audio MIDI Setup, a privacy toggle — so offer to walk
them through it, section by section, with the warnings from the table above. After setup, and once
they're fine with a sound, run `preflight.py --audio-test`: it confirms that sound played through the
system output actually reaches BlackHole.

### 2. Launch and check the geometry — before involving the person

```
python3 <skill dir>/scripts/record.py launch --project <path.uproject> --map <map package path> --res 1920x1080
```

- It launches through `open -n`, waits for `Load map complete` in the engine log, measures the
  game window and writes `check.png`, a crop of the game's client area. The game keeps running.
- If the UI you need appears *after* the map loads (a widget created on a timer, a panel spawned by
  the player controller), pass `--ready-marker` with a log line that proves it exists. Waiting for
  the map alone once produced a whole 45-second take in which the panel only appeared at the end.
- The map is a package path: `/Game/Maps/Arena`, or `/PluginName/Demo/Maps/Demo` for plugin content.

**Read `check.png` yourself** before going further. Confirm the scene rendered (not black, not a
loading screen), every HUD element and panel the user cares about is visible, and there is no title
bar or desktop at the edges. If it's wrong, `record.py stop --session <dir>` and fix the cause —
don't adjust crop numbers by hand. Also note `active_app` in the session: if false, the game's
hotkeys won't respond until someone clicks inside its window.

### 3. Brief the person, then take

Once the game is up it covers the terminal, so the person at the machine can't read chat. Tell them
**before** the take what will happen: a spoken "get ready", then "three, two, one, go"; after "go"
they have N seconds for camera moves (name the project's controls if you know them); they
shouldn't close the game window — it closes by itself when the voice says "done". Then:

```
python3 <skill dir>/scripts/record.py take --session <session dir> --duration 30
```

`--no-audio` records silently when BlackHole isn't available and the user accepts that.
`--no-countdown` suits unattended captures with a static camera.

### 4. Finalize and verify

```
python3 <skill dir>/scripts/finalize.py --session <session dir>
```

It trims just after "go", crops the client area with a small inset, scales to the requested size,
normalises loudness (−14 LUFS, true peak −2 dB before AAC), encodes H.264/AAC and then checks the
frame count, loudness and audio/video sync. Exit code 3 means a check failed; the `PROBLEMS` line
says which. Then **read the contact sheet** it writes: four frames spread across the clip should
show the camera moves, the UI and clean edges.

Re-running `finalize.py` is cheap and needs no game — change `--duration`, `--start`, `--lufs` or
`--output` as often as the user likes. The raw capture stays in the session directory.

## Frame dump

```
python3 <skill dir>/scripts/dump.py --project <path.uproject> --map <map package path> --res 1920x1080 --duration 30
```

It renders 3–4× slower than real time and writes about 2 GB of PNG per 30 s at 1080p, deleted
afterwards unless `--keep-frames`. It aborts if no new frame arrives for 90 s — that has happened on
a real run with no identifiable cause, and a retry worked. Read the contact sheet afterwards, and
remind the user that sound and any UMG/Slate UI are absent by design of the engine.

## Rules that aren't obvious

- **For a real-time take, never start the game by running the binary from the terminal.** macOS
  14+ won't make it the active app. Mouse wheel and right-drag still reach its window, so camera
  controls work — but every hotkey is silently dead, which looks exactly like a bug in the
  project's input code. `record.py` launches through `open -n` for this reason. (A frame dump
  doesn't need keyboard focus, so `dump.py` may use the binary directly.)
- **`-fullscreen` does not give you a resolution on Mac.** It renders at the display's native size
  and ignores `-ResX/-ResY`, so a Canvas HUD laid out in pixels comes out at a different scale than
  a 1920×1080 window. Record a window at the target size; use `--fullscreen` only when native
  resolution is what's wanted.
- **Wait for the log, not the clock.** A cold shader cache can hold the first frames back for
  minutes; a warm one loads the map in under a second.
- **Look at frames.** Captures that were missing the UI, showed only the desktop (the game had been
  closed before recording started), or had rounded desktop-coloured corners all "succeeded".

When anything unexpected happens, read `references/troubleshooting.md` — every failure met so far,
with its symptom, cause and fix.

## Reporting back

Tell the user: the output path; resolution, fps, frame count and duration; loudness and true peak
when there is audio; what you verified by looking (UI present, camera moves, clean edges); and
anything that deviated — dropped frames, a shortened clip, the unverified Retina path. Output lands
in `<project>/Saved/VideoCaptures/<name>-<timestamp>/`, which Unreal projects already keep out of git.
