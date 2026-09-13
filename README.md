# ue-video-capture

A [Claude Code](https://claude.com/claude-code) skill that records videos of Unreal Engine levels and demo scenes on macOS — with game audio, UMG/Slate UI and live camera moves — at an exact resolution and length, ready for YouTube, Fab listings or trailers.

## The idea

Recording a UE scene looks like a one-liner and isn't. On the way to one 30-second clip, every obvious approach produced a wrong video while reporting success:

- the engine's own frame dump (`-DUMPMOVIE`) silently leaves out every UMG/Slate widget and has no audio;
- a game started from the terminal ignores all hotkeys, because macOS won't make it the active app — while mouse camera controls keep working, so it looks like a bug in the project;
- `-fullscreen` on Mac ignores the requested resolution and changes the HUD's scale;
- a window crop keeps macOS's rounded corners, with the desktop showing through;
- loudness normalised to −1 dB true peak lands above it once AAC is encoded.

This skill packages the approach that survived all of that, verified on real hardware: two capture modes chosen by what the clip needs, a two-phase real-time recording with a geometry check before anyone performs camera moves, a spoken countdown for the person at the machine (who can't see the terminal once the game is up), and a finalize step that proves frame count, loudness and sync instead of assuming them.

## Install

```bash
git clone https://github.com/logdok/ue-video-capture.git ~/.claude/skills/ue-video-capture
```

User-level skill — available in any Unreal project once installed.

Requirements: macOS 14 or later, [ffmpeg](https://ffmpeg.org) (`brew install ffmpeg`), Xcode or its Command Line Tools. Recording with sound also needs [BlackHole](https://github.com/ExistentialAudio/BlackHole) and a one-time setup that the skill walks you through (`references/setup.md`).

## Use

Ask for what you want — *"record 30 seconds of the turret demo map with sound, I'll move the camera"* or *"запиши видео сцены с дронами"* — and the skill triggers automatically. It also triggers when a recording has gone wrong, for example frames missing the HUD or hotkeys that don't respond.

See [`SKILL.md`](SKILL.md) for the workflow and [`references/troubleshooting.md`](references/troubleshooting.md) for every failure met so far, with its evidence.

## Layout

```
SKILL.md                         workflow: choosing a mode, launching, checking, taking, finalizing
scripts/preflight.py             is this Mac ready? names exactly what is missing
scripts/record.py                real-time capture: launch (measure window, check.png) / take / stop
scripts/finalize.py              trim, inset crop, scale, two-pass loudness, encode, verify, contact sheet
scripts/dump.py                  deterministic silent capture through -DUMPMOVIE
scripts/window_rect.swift        window bounds, active-app state and displays via CoreGraphics
scripts/uevc_common.py           shared helpers
references/setup.md              BlackHole, Multi-Output Device, Screen Recording permission
references/troubleshooting.md    symptoms, causes and fixes
```

## Status

Verified on UE 5.8, macOS 26, Apple Silicon, a 2560×1440 external display at scale 1.0. Retina displays are handled in code but not yet verified on hardware.
