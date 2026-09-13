# Troubleshooting — every failure met so far

Each entry: what you see, why it happens (with the evidence that established it), what to do.
All were observed on UE 5.8 / macOS 26 / Apple Silicon unless marked otherwise.

## Contents

1. Frames are missing the UMG / Slate UI
2. A frame dump has no sound
3. Hotkeys do nothing, but mouse camera controls work
4. HUD is smaller or larger than expected in fullscreen
5. Desktop shows in the bottom corners; thin light line along the top
6. True peak above −1 dB after normalising to −1
7. A remux cut frames off the end
8. No engine log where you expected it
9. ffmpeg hangs on screen capture
10. BlackHole installed but not listed
11. Captured audio is silent or nearly silent
12. The first seconds of a capture miss a sound
13. The UI appeared late / the level took minutes to come up
14. A frame dump stalls at some frame count
15. The recording shows the desktop, not the game
16. `open` brought the editor to the front instead of starting the game
17. Tools that behave differently on macOS
18. The audio track ends a quarter second before the video

---

## 1. Frames are missing the UMG / Slate UI

**Seen:** a `-DUMPMOVIE` capture showed the Canvas HUD panels but not a Slate panel with graphs,
although the engine log said the panel was created and it was visible on screen.

**Cause:** `Engine/Source/Runtime/Engine/Private/GameViewportClient.cpp`:

```cpp
bool bShowUI = false;
if (!GIsDumpingMovie && (FScreenshotRequest::ShouldShowUI() && WindowPtr.IsValid()))
{
    bShowUI = true;
}
```

While dumping, `bShowUI` is always false, so only the viewport render target is saved: the 3D scene
plus anything drawn on the Canvas (`AHUD::DrawHUD`). Widgets added with `AddViewportWidgetContent` or
UMG `AddToViewport` are composited by Slate afterwards and never reach the file.

**Fix:** use real-time capture (`record.py`). No flag makes the dump include them.

## 2. A frame dump has no sound

**Cause:** `-DUMPMOVIE` writes images only. Recording audio alongside doesn't work either:
`-BENCHMARK` makes the simulation run 3–4× slower than real time while the audio engine plays in
real time, so 30 s of gameplay would stretch over about two minutes of sound.

**Fix:** real-time capture.

## 3. Hotkeys do nothing, but mouse camera controls work

**Seen:** in a game started from the terminal, right-drag orbit and wheel zoom worked; V, P and
Space did nothing. It looked exactly like broken input bindings.

**Cause:** macOS 14+ "cooperative activation". An app launched by a background terminal process is
not made the active app, and a later activation request is refused — measured:
`NSRunningApplication.activate` returned `requested=true isActive=false`. Scroll and right-mouse
events are delivered to the window under the cursor even for an inactive app; keyboard events go
only to the active app (in that session: the terminal).

**Evidence the code was fine:** the same build launched with `open -n` reported `isActive=true`, and
the panel toggled on every press, verified frame by frame.

**Fix:** launch with `open -n -a …/UnrealEditor.app --args …` (what `record.py` does). If a game is
already running inactive, one left click inside its window activates it.

## 4. HUD is smaller or larger than expected in fullscreen

**Seen:** `-ResX=1920 -ResY=1080 -fullscreen` on a 2560×1440 display produced Canvas HUD panels a
quarter smaller than in a 1920×1080 capture. Log: `LogMac: Screen: DELL AW2723DF, Resolution: 2560x1440`.

**Cause:** fullscreen on Mac renders at the display's native resolution and ignores `-ResX/-ResY`.
A Canvas HUD laid out in pixels scales with that; a DPI-scaled Slate panel doesn't.

**Fix:** record a window at the target size and crop (the default). Use fullscreen only when native
resolution is what's wanted.

## 5. Desktop shows in the bottom corners; thin light line along the top

**Seen:** cropping exactly to the client area left rounded, desktop-coloured bottom corners (radius
about 12 px) and a 1 px light line at the top from the title bar separator.

**Cause:** macOS windows have rounded bottom corners; the client area is square.

**Fix:** `finalize.py` crops with an inset — 2 pt top, 16 pt bottom, sides from the aspect ratio —
and scales back up (about 1.7% at 1080p, invisible). Measured client area for a 1920×1080 window
placed with `-WinY=150`: window frame at y=117, height 1112, so a 32 pt title bar and content from
y=149. The row at y=148 was the separator (grey 224), y=149 already scene colour.

## 6. True peak above −1 dB after normalising to −1

**Seen:** raw gunfire audio had a true peak of +1.77 dBTP (inter-sample peaks; sample peak was only
−0.18 dB with two samples at the peak, so no actual clipping). Two-pass `loudnorm` with `TP=-1`
produced −0.3 dBTP after AAC encoding.

**Cause:** lossy encoding adds overshoot after the limiter has run.

**Fix:** `TP=-2` gave −1.2 dBTP after AAC — the default in `finalize.py`. Note that loudnorm falls
back from linear to dynamic mode when the target can't be reached linearly; that's fine for
continuous game audio.

## 7. A remux cut frames off the end

**Seen:** remuxing new audio onto an existing video with `-shortest` turned 900 frames into 890.

**Cause:** the re-encoded audio came out slightly shorter; `-shortest` trims the video to match.

**Fix:** never use `-shortest` here; set the length with `-t` and verify with
`ffprobe -count_packets` (the container's `nb_frames` can be stale).

## 8. No engine log where you expected it

**Seen:** stdout of a game launched from the terminal contained only UnrealBuildTool's platform
validation lines; `Saved/Logs/` had nothing new either.

**Cause:** a game run with the editor binary logs to `~/Library/Logs/Unreal Engine/<Target>Editor/`,
and parallel runs get numbered names (`HostProject_2.log`, …).

**Fix:** always pass `-abslog=<path>` (the scripts do).

## 9. ffmpeg hangs on screen capture

**Cause:** no Screen & System Audio Recording permission. ffmpeg waits indefinitely instead of
failing.

**Fix:** `references/setup.md` §3. The scripts wrap every screen grab in a watchdog. Note that macOS
has no `timeout` command; use a background process plus `kill` if you test by hand.

## 10. BlackHole installed but not listed

**Cause:** CoreAudio loads HAL plug-ins at start-up. `brew install` places the driver but doesn't
always restart the daemon.

**Fix:** `sudo killall coreaudiod`, or reboot.

## 11. Captured audio is silent or nearly silent

**Checks, in order:**
- Is the system output the Multi-Output Device? Games play to the default output; if it's the
  speakers, BlackHole receives nothing. `preflight.py` reports the current output.
- Does sound reach BlackHole at all? `preflight.py --audio-test` expects a peak around −6 dB.
- Don't test with `say -a "BlackHole 2ch"`: on macOS 26 it crashes on device-name matching and
  records nothing, which looks like a routing failure. Play through the default output instead.

## 12. The first seconds of a capture miss a sound

**Seen:** two short system sounds played one second after starting a combined screen+audio capture
were absent (peak −50 dB); the same test on audio-only capture peaked at −5.7 dB.

**Cause:** opening screen and audio devices together takes over a second before samples flow.

**Fix:** start capturing at least 2 s before anything important — `record.py` has a pre-roll and
puts the spoken countdown inside it, then `finalize.py` cuts everything before "go".

## 13. The UI appeared late / the level took minutes to come up

**Seen:** on a first launch of a map, a panel created on a retry timer appeared only after most of
a dump had been captured; on later launches `Load map complete` and the panel's log line arrived in
the same millisecond.

**Cause:** shader and pipeline-state compilation on a cold cache.

**Fix:** wait for a log line, never a fixed sleep; when the important UI is created separately,
pass its own log line as `--ready-marker`. A first launch just to warm the cache also helps.

## 14. A frame dump stalls at some frame count

**Seen:** once, a dump stopped producing frames at 630 and stayed there for minutes; the process
kept running. The cause was not identified.

**Fix:** `dump.py` aborts after `--stall-timeout` seconds without a new frame. Retrying worked.

## 15. The recording shows the desktop, not the game

**Seen:** a recording contained only the desktop and other windows. The engine log showed the game
window had been closed by hand (`Window … being destroyed` → `RequestExit … ViewportClosed`) before
the capture started.

**Cause:** the person at the machine couldn't see instructions and closed the window; separately, a
game started inactive can open behind other windows.

**Fix:** brief the person before the take; don't start the take until `check.png` has been
inspected; `record.py take` refuses to run when the game process is gone.

## 16. `open` brought the editor to the front instead of starting the game

**Cause:** without `-n`, `open -a UnrealEditor.app` activates an already running instance.

**Fix:** always `open -n`.

## 17. Tools that behave differently on macOS

- `od -w` doesn't exist in BSD `od`; parse raw bytes with Python instead.
- `timeout` doesn't exist.
- avfoundation rejects `yuv420p` for screen capture; use `-pixel_format uyvy422`.
- Retina displays (unverified): window geometry is reported in points, capture is in pixels.
  `record.py` derives the scale from a captured frame, but this path hasn't been run on real
  hardware — inspect `check.png` especially carefully there.

## 18. The audio track ends a quarter second before the video

**Seen:** a clip encoded with `-ss 6.71 -t 30 -i raw.mov` and a two-pass `loudnorm` had 900 video
frames (30.000 s) but only 29.745 s of audio. The same command without `loudnorm` gave 30.000 s.
Nothing warned; it was caught only by comparing stream durations.

**Cause:** `loudnorm` keeps a look-ahead buffer and drops it when its input ends. Here it fell back
to dynamic mode, but the lost tail comes from where the input is cut.

**Fix:** read the input a little longer than the clip and cut on the output. Two traps on the way:
an output `-t 30` then cut one *video* frame (899 — the first frame's timestamp is 1/30 s, not 0),
so `finalize.py` cuts video with `-frames:v` and audio with `atrim=duration=` after `loudnorm`.
Result: 900 frames, 30.000 s of audio, and pixels identical to the earlier encode (PSNR inf).
