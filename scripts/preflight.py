#!/usr/bin/env python3
"""Checks whether this Mac can record Unreal Engine video, and says exactly what is missing.

    python3 preflight.py              # checks only, nothing audible
    python3 preflight.py --audio-test # also speaks a phrase and confirms it reaches BlackHole
    python3 preflight.py --json       # machine-readable result

Every check here exists because its failure mode is silent or misleading: ffmpeg hangs instead of
erroring without Screen Recording permission, a freshly installed BlackHole is invisible until
coreaudiod restarts, and audio captured with the wrong default output is just silence.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uevc_common as c  # noqa: E402


def default_output_device() -> str | None:
    result = subprocess.run(["system_profiler", "SPAudioDataType", "-json"], capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for group in data.get("SPAudioDataType", []):
        for item in group.get("_items", []):
            if item.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                return item.get("_name")
    return None


def audio_reaches_blackhole(audio_index: int) -> tuple[bool, float | None]:
    """Speaks through the system output while recording BlackHole alone.

    Audio-only capture initialises fast; a combined screen+audio capture can take over a second to
    start, which silently swallows a short test sound — so this test deliberately records audio only."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "blackhole.wav"
        recorder = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "avfoundation", "-i", f":{audio_index}", "-t", "4", str(wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        # `say -a <device>` crashes on device-name matching on macOS 26; route through the default output instead.
        subprocess.run(["say", "testing one two three"])
        try:
            recorder.wait(timeout=15)
        except subprocess.TimeoutExpired:
            recorder.kill()
            return False, None
        peak = c.max_volume(wav) if wav.exists() else None
        return (peak is not None and peak > -30.0), peak


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audio-test", action="store_true", help="speak a phrase and verify it reaches BlackHole")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()
    c.require_macos()

    report: dict = {"checks": {}, "blocking": [], "notes": []}
    checks = report["checks"]

    for tool in ("ffmpeg", "ffprobe", "say"):
        checks[tool] = shutil.which(tool) is not None
        if not checks[tool]:
            report["blocking"].append(f"`{tool}` is missing" + (" — `brew install ffmpeg`" if tool != "say" else ""))

    swiftc = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True, text=True)
    checks["swiftc"] = swiftc.returncode == 0
    if not checks["swiftc"]:
        report["blocking"].append("`swiftc` is missing — install the Xcode Command Line Tools (`xcode-select --install`)")

    if not (checks["ffmpeg"] and checks["ffprobe"]):
        return finish(report, args.json)

    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    checks["h264_videotoolbox"] = "h264_videotoolbox" in encoders
    if not checks["h264_videotoolbox"]:
        report["notes"].append("h264_videotoolbox encoder unavailable; record.py will fall back to libx264 ultrafast, which may drop frames at high resolutions")

    devices = c.list_avfoundation_devices()
    report["devices"] = devices
    screen_index = c.find_screen_index(devices, 0)
    audio_index = c.find_audio_index(devices, "BlackHole")
    report["screen_index"] = screen_index
    report["blackhole_index"] = audio_index

    if screen_index is None:
        report["blocking"].append("no 'Capture screen 0' avfoundation device")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            checks["screen_recording_permission"] = c.grab_screen_frame(screen_index, Path(tmp) / "probe.png", 20.0)
        if not checks["screen_recording_permission"]:
            report["blocking"].append(
                "screen capture hangs or fails — grant Screen & System Audio Recording to the terminal app "
                "(references/setup.md §3). macOS then offers to quit and reopen that app, which ends the current "
                "Claude Code session: let the user choose when")

    checks["blackhole_visible"] = audio_index is not None
    if audio_index is None:
        installed = Path("/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver").exists()
        report["blocking_for_audio"] = (
            "BlackHole is installed but not loaded — restart CoreAudio: `sudo killall coreaudiod` (references/setup.md §1)"
            if installed else "BlackHole is not installed — `brew install blackhole-2ch` (references/setup.md §1)"
        )

    output = default_output_device()
    report["default_output"] = output
    checks["output_routes_to_blackhole"] = bool(output) and ("multi-output" in output.lower() or "blackhole" in output.lower())
    if audio_index is not None and not checks["output_routes_to_blackhole"]:
        report["blocking_for_audio"] = (
            f"system output is '{output}', so game audio never reaches BlackHole — create and select a "
            f"Multi-Output Device (references/setup.md §2)")

    if args.audio_test and audio_index is not None:
        ok, peak = audio_reaches_blackhole(audio_index)
        checks["audio_reaches_blackhole"] = ok
        report["audio_test_peak_db"] = peak
        if not ok:
            report["blocking_for_audio"] = f"speech played through the system output peaked at {peak} dB in BlackHole — audio routing is broken (references/setup.md §2)"

    info = c.window_info(0)
    report["displays"] = info["displays"]
    for display in info["displays"]:
        if display["scale"] != 1.0:
            report["notes"].append(
                f"display '{display['name']}' has backing scale {display['scale']}: window geometry is in points and "
                f"capture is in pixels. record.py handles the conversion, but this path is unverified — inspect check.png carefully")

    return finish(report, args.json)


def finish(report: dict, as_json: bool) -> int:
    realtime_ready = not report["blocking"]
    report["ready_for_dump"] = all(report["checks"].get(k) for k in ("ffmpeg", "ffprobe"))
    report["ready_for_realtime_video"] = realtime_ready
    report["ready_for_realtime_audio"] = realtime_ready and "blocking_for_audio" not in report and report["checks"].get("blackhole_visible", False)

    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("ue-video-capture preflight")
        for name, value in report["checks"].items():
            print(f"  {'ok ' if value else 'NO '} {name}")
        if report.get("screen_index") is not None:
            print(f"  screen capture device: {report['screen_index']}   BlackHole audio device: {report.get('blackhole_index')}")
        print(f"  default output: {report.get('default_output')}")
        for display in report.get("displays", []):
            print(f"  display: {display['name']} {display['w']}x{display['h']} pt, scale {display['scale']}{' (main)' if display['main'] else ''}")
        for item in report["blocking"]:
            print(f"BLOCKING: {item}")
        if "blocking_for_audio" in report:
            print(f"AUDIO: {report['blocking_for_audio']}")
        for note in report["notes"]:
            print(f"note: {note}")
        print(f"ready: dump={report['ready_for_dump']} realtime_video={report['ready_for_realtime_video']} "
              f"realtime_audio={report['ready_for_realtime_audio']}")
    return 0 if realtime_ready else 2


if __name__ == "__main__":
    sys.exit(main())
