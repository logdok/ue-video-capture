#!/usr/bin/env python3
"""Deterministic, silent capture through the engine's own frame dump (-DUMPMOVIE).

    dump.py --project P.uproject --map /Game/Maps/Arena [--res 1920x1080] [--duration 30]

The engine renders each frame at a fixed 1/fps step and saves it as PNG, however long rendering
takes, so the clip has exactly the requested length and no dropped frames — and needs no macOS
permissions at all. Two hard limits, both by design of the engine, not fixable here:

  * no audio track;
  * Slate / UMG widgets added to the viewport are NOT in the frames. The engine only composites UI
    into a screenshot when `!GIsDumpingMovie` (GameViewportClient.cpp). A HUD drawn on the Canvas
    (AHUD::DrawHUD) is captured; a UMG widget or AddViewportWidgetContent panel is not.

If the scene needs sound, UMG/Slate UI or live camera control, use record.py instead.
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uevc_common as c  # noqa: E402


def frame_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/MovieFrame*.png")) if root.exists() else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--engine")
    parser.add_argument("--res", default="1920x1080")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--name")
    parser.add_argument("--out-dir", help="default <project>/Saved/VideoCaptures")
    parser.add_argument("--ready-marker", default="Load map complete")
    parser.add_argument("--settle", type=float, default=2.0, help="simulated seconds to discard after the marker")
    parser.add_argument("--timeout", type=float, default=900.0, help="overall seconds before giving up")
    parser.add_argument("--stall-timeout", type=float, default=90.0, help="abort when no new frame appears for this long")
    parser.add_argument("--keep-frames", action="store_true", help="keep the PNG frames (about 2 GB per 30 s at 1080p)")
    parser.add_argument("--clear-old-frames", action="store_true",
                        help="delete MovieFrame PNGs left in Saved/Screenshots by an earlier dump (ask the user first)")
    parser.add_argument("--extra-arg", action="append")
    args = parser.parse_args()
    c.require_macos()
    c.require_tool("ffmpeg")

    uproject = Path(args.project).expanduser().resolve()
    engine = c.resolve_engine_root(uproject, args.engine)
    width, height = (int(v) for v in args.res.lower().split("x"))
    name = args.name or args.map.rstrip("/").split("/")[-1].split(".")[0]
    out_root = Path(args.out_dir).expanduser() if args.out_dir else uproject.parent / "Saved" / "VideoCaptures"
    out_root.mkdir(parents=True, exist_ok=True)

    # Old frames would be counted as new ones, but they may be someone's earlier capture: delete them
    # only when the user has agreed to it.
    shots = uproject.parent / "Saved" / "Screenshots"
    stale = frame_files(shots)
    if stale and not args.clear_old_frames:
        c.die(f"{len(stale)} MovieFrame PNGs from an earlier dump are in {shots}. Ask the user whether they can be "
              f"deleted, then re-run with --clear-old-frames (or move them away first).", code=4)
    for frame in stale:
        frame.unlink()
    if stale:
        print(f"removed {len(stale)} old MovieFrame PNGs from {shots}")

    # The engine log does not go to stdout when launched this way; without -abslog the only copy
    # lands in ~/Library/Logs/Unreal Engine/<Target>/ under a numbered name.
    log = out_root / f"{name}-dump-{secrets.token_hex(4)}.log"
    binary = engine / "Engine/Binaries/Mac/UnrealEditor"
    cmd = [str(binary), str(uproject), args.map, "-game", f"-ResX={width}", f"-ResY={height}", "-windowed", "-ForceRes",
           "-DUMPMOVIE", "-BENCHMARK", f"-FPS={args.fps}", "-NOTEXTURESTREAMING", "-nosound", "-unattended", "-nosplash",
           f"-abslog={log}"] + list(args.extra_arg or [])
    # A frame dump needs no keyboard focus, so the bare binary is fine here (record.py cannot do this).
    game = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    started = time.time()

    def alive() -> bool:
        return game.poll() is None

    c.wait_for_log_marker(log, args.ready_marker, alive, args.timeout)
    first = len(frame_files(shots)) + int(args.settle * args.fps)
    needed = first + round(args.duration * args.fps) + args.fps // 2
    print(f"level ready; keeping frames {first}..{needed} (the dump runs slower than real time)")

    last_count, last_change = -1, time.time()
    while True:
        count = len(frame_files(shots))
        if count != last_count:
            last_count, last_change = count, time.time()
            print(f"  {count}/{needed} frames")
        if count >= needed:
            break
        if not alive():
            c.die(f"the game exited after {count} frames; see {log}")
        if time.time() - last_change > args.stall_timeout:
            game.terminate()
            c.die(f"no new frame for {args.stall_timeout:.0f}s at {count} frames — the dump stalled. This has been "
                  f"seen on real runs without an identified cause; retrying usually helps.")
        if time.time() - started > args.timeout:
            game.terminate()
            c.die(f"overall timeout at {count} frames")
        time.sleep(5)

    game.terminate()
    try:
        game.wait(timeout=30)
    except subprocess.TimeoutExpired:
        game.kill()

    frames = frame_files(shots)
    frame_dir = frames[0].parent
    output = out_root / f"{name}-{height}p{args.fps}-dump.mp4"
    total = round(args.duration * args.fps)
    c.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-framerate", str(args.fps), "-start_number", str(first),
           "-i", str(frame_dir / "MovieFrame%05d.png"), "-frames:v", str(total),
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)])

    written = c.count_video_frames(output)
    sheet = output.with_suffix(".contact.jpg")
    picks = "+".join(f"eq(n\\,{int(written * f)})" for f in (0.05, 0.35, 0.65, 0.95))
    c.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(output),
           "-vf", f"select='{picks}',scale=960:-2,tile=2x2", "-frames:v", "1", "-fps_mode", "passthrough", str(sheet)])

    if not args.keep_frames:
        for frame in frames:
            frame.unlink()

    report = {"output": str(output), "frames": written, "expected_frames": total, "fps": args.fps,
              "resolution": [width, height], "audio": False, "slate_ui_captured": False, "contact_sheet": str(sheet)}
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if written != total:
        print(f"PROBLEM: {written} frames written, expected {total}", file=sys.stderr)
        return 3
    print(f"NEXT: look at {sheet}. Any UMG/Slate UI will be missing — that is a -DUMPMOVIE limit, not a bug.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
