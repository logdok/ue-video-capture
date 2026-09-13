#!/usr/bin/env python3
"""Turns a raw real-time capture into a finished clip, and proves it came out right.

    finalize.py --session <dir> [--duration 30] [--output clip.mp4]

Trims at the spoken "go", crops to the game's client area with a small inset, scales to the
requested resolution, normalises loudness in two passes, encodes H.264/AAC, then verifies frame
count, loudness and stream sync, and writes a contact sheet to look at.

Each default below corrects something that went wrong on real footage:
  * inset crop    macOS window corners are rounded (~12 px) and show the desktop; the top edge
                  carries a 1 px line from the title bar. A plain client-area crop keeps both.
  * true peak -2  targeting -1 dBTP came out at -0.3 after AAC encoding; the codec adds overshoot.
  * no -shortest  a remux with -shortest silently cut 10 frames off a 900-frame clip.
  * input margin  loudnorm drops its look-ahead at the end of the input, leaving the audio 0.25 s
                  short of the video; the input is read a little longer and cut on the output.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uevc_common as c  # noqa: E402


def even(value: float) -> int:
    number = int(value)
    return number - (number % 2)


def inset_crop(content: dict, out_w: int, out_h: int, top: int, bottom: int) -> dict:
    """Largest even-sized rectangle of the output aspect ratio inside the client area, minus the
    top and bottom insets, centred horizontally. Side insets fall out of the aspect ratio."""
    aspect = out_w / out_h
    usable_h = content["h"] - top - bottom
    crop_h = even(usable_h)
    crop_w = even(round(crop_h * aspect))
    if crop_w > content["w"]:
        crop_w = even(content["w"])
        crop_h = even(round(crop_w / aspect))
    return {
        "w": crop_w, "h": crop_h,
        "x": content["x"] + (content["w"] - crop_w) // 2,
        "y": content["y"] + top + (usable_h - crop_h) // 2,
    }


def loudnorm_filter(raw: Path, start: float, duration: float, lufs: float, true_peak: float, lra: float) -> str:
    """Two-pass loudnorm: measure first, then apply with the measurements so the gain is computed
    for this clip rather than estimated on the fly."""
    measure = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(raw), "-vn",
         "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA={lra}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", measure.stderr, re.S)
    if not blocks:
        c.die("loudness measurement failed:\n" + measure.stderr[-1500:])
    m = json.loads(blocks[-1])
    return (f"loudnorm=I={lufs}:TP={true_peak}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def contact_sheet(video: Path, frames: int, output: Path) -> None:
    picks = [int(frames * f) for f in (0.05, 0.35, 0.65, 0.95)]
    select = "+".join(f"eq(n\\,{n})" for n in picks)
    c.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(video),
           "-vf", f"select='{select}',scale=960:-2,tile=2x2", "-frames:v", "1", "-fps_mode", "passthrough", str(output)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", required=True, help="session directory or session.json from record.py")
    parser.add_argument("--duration", type=float, help="seconds to keep; default is the take's duration")
    parser.add_argument("--start-pad", type=float, default=0.3, help="seconds after 'go' to start the clip")
    parser.add_argument("--start", type=float, help="absolute start in the raw file, overriding go + pad")
    parser.add_argument("--no-inset", action="store_true", help="crop exactly to the client area (fullscreen captures do this anyway)")
    parser.add_argument("--inset-top", type=float, default=2.0, help="points trimmed at the top")
    parser.add_argument("--inset-bottom", type=float, default=16.0, help="points trimmed at the bottom (window corner radius)")
    parser.add_argument("--lufs", type=float, default=-14.0, help="integrated loudness target (YouTube normalises to -14)")
    parser.add_argument("--true-peak", type=float, default=-2.0, help="true peak ceiling before AAC encoding")
    parser.add_argument("--lra", type=float, default=11.0)
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="slow")
    parser.add_argument("--output", help="default <session>/<name>-<height>p<fps>.mp4")
    args = parser.parse_args()
    c.require_macos()

    session_file = Path(args.session).expanduser()
    session_file = session_file / "session.json" if session_file.is_dir() else session_file
    session = json.loads(session_file.read_text(encoding="utf-8"))
    take = session.get("take")
    if not take:
        c.die("this session has no take yet — run `record.py take` first")

    raw = Path(take["raw"])
    out_w, out_h = session["res"]
    fps = session["fps"]
    duration = args.duration or take["duration"]
    start = args.start if args.start is not None else take["go_offset"] + args.start_pad

    available = min(d for d in (take.get("video_duration"), take.get("audio_duration")) if d) - start
    if available < duration - 0.05:
        c.warn(f"only {available:.2f}s of footage after the start point; shortening the clip")
        duration = int(available * fps) / fps

    content = session["content_px"]
    scale = session.get("scale", 1.0)
    if args.no_inset or session.get("fullscreen"):
        crop = dict(content)
    else:
        crop = inset_crop(content, out_w, out_h, round(args.inset_top * scale), round(args.inset_bottom * scale))

    output = Path(args.output).expanduser() if args.output else session_file.parent / f"{session['name']}-{out_h}p{fps}.mp4"
    video_filter = f"crop={crop['w']}:{crop['h']}:{crop['x']}:{crop['y']},scale={out_w}:{out_h}:flags=lanczos"

    # loudnorm holds back its look-ahead and drops it at the end of the input: with the input cut to
    # exactly 30 s the audio came out 29.745 s. Read up to a second more, then cut each stream on its
    # own terms — video by frame count (an output -t lost a frame to the first frame's pts offset),
    # audio with atrim after loudnorm.
    lookahead = max(0.0, min(1.0, available - duration))
    expected = round(duration * fps)
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{duration + lookahead:.3f}",
           "-i", str(raw), "-frames:v", str(expected),
           "-vf", video_filter, "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p"]
    if take.get("audio"):
        audio_filter = loudnorm_filter(raw, start, duration, args.lufs, args.true_peak, args.lra)
        cmd += ["-af", f"{audio_filter},atrim=duration={duration:.3f}", "-ar", "48000", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", str(output)]
    print(f"encoding {duration:g}s from {start:.2f}s, crop {crop['w']}x{crop['h']}+{crop['x']}+{crop['y']} -> {out_w}x{out_h}")
    c.run(cmd)

    frames = c.count_video_frames(output)
    probe = c.ffprobe_json(output)
    durations = {s["codec_type"]: float(s.get("duration", 0) or 0) for s in probe["streams"]}
    report = {
        "output": str(output), "width": out_w, "height": out_h, "fps": fps,
        "frames": frames, "expected_frames": expected, "duration_s": durations.get("video"),
        "start_s": start, "crop": crop, "audio": bool(take.get("audio")),
    }
    problems = []
    if abs(frames - expected) > 1:
        problems.append(f"{frames} frames, expected {expected}")
    if take.get("audio"):
        loud = c.measure_loudness(output)
        report.update(loud)
        drift = abs(durations.get("video", 0) - durations.get("audio", 0))
        report["av_duration_gap_s"] = drift
        if loud["integrated_lufs"] is not None and abs(loud["integrated_lufs"] - args.lufs) > 1.0:
            problems.append(f"loudness {loud['integrated_lufs']} LUFS, target {args.lufs}")
        if loud["true_peak_dbfs"] is not None and loud["true_peak_dbfs"] > -1.0:
            problems.append(f"true peak {loud['true_peak_dbfs']} dBFS above -1 — lower --true-peak and re-run")
        if drift > 0.1:
            problems.append(f"audio and video durations differ by {drift:.2f}s")
    report["problems"] = problems

    sheet = output.with_suffix(".contact.jpg")
    contact_sheet(output, frames, sheet)
    report["contact_sheet"] = str(sheet)
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    if problems:
        print("PROBLEMS: " + "; ".join(problems), file=sys.stderr)
        return 3
    print(f"NEXT: look at {sheet} — camera work, UI and clean edges in all four frames.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
