"""Shared helpers for the ue-video-capture scripts. macOS only, Python standard library only."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path.home() / "Library" / "Caches" / "ue-video-capture"


def die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def require_macos() -> None:
    if sys.platform != "darwin":
        die("ue-video-capture only supports macOS (it relies on avfoundation, CoreGraphics and `say`).")


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        die(f"`{name}` not found on PATH. Install it (ffmpeg: `brew install ffmpeg`) and retry.")
    return path


def run(cmd: list[str], timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        die(f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stderr.strip()[-2000:]}")
    return result


# --- ffmpeg / avfoundation ------------------------------------------------------------------------

def list_avfoundation_devices() -> dict[str, list[tuple[int, str]]]:
    """Video and audio capture devices as avfoundation numbers them. The numbering changes when
    hardware is plugged in or a virtual driver is installed, so never hardcode an index."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    devices: dict[str, list[tuple[int, str]]] = {"video": [], "audio": []}
    section = None
    for line in result.stderr.splitlines():
        if "AVFoundation video devices" in line:
            section = "video"
            continue
        if "AVFoundation audio devices" in line:
            section = "audio"
            continue
        match = re.search(r"\]\s\[(\d+)\]\s(.+)$", line)
        if section and match:
            devices[section].append((int(match.group(1)), match.group(2).strip()))
    return devices


def find_screen_index(devices: dict, screen_number: int = 0) -> int | None:
    for index, name in devices["video"]:
        if name == f"Capture screen {screen_number}":
            return index
    return None


def find_audio_index(devices: dict, needle: str = "BlackHole") -> int | None:
    for index, name in devices["audio"]:
        if needle.lower() in name.lower():
            return index
    return None


def ffprobe_json(path: Path) -> dict:
    result = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(result.stdout)


def count_video_frames(path: Path) -> int:
    """Counts packets rather than trusting the container's nb_frames, which a remux can leave stale."""
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path),
    ])
    return int(result.stdout.strip() or 0)


def measure_loudness(path: Path) -> dict[str, float | None]:
    """Integrated loudness (LUFS) and true peak (dBFS) via EBU R128."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    summary = result.stderr.split("Summary:")[-1]
    integrated = re.search(r"I:\s+(-?[\d.]+|-inf)\s+LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS", summary)

    def as_float(match):
        if not match:
            return None
        return float("-inf") if match.group(1) == "-inf" else float(match.group(1))

    return {"integrated_lufs": as_float(integrated), "true_peak_dbfs": as_float(peak)}


def max_volume(path: Path) -> float | None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    match = re.search(r"max_volume:\s+(-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else None


def image_size(path: Path) -> tuple[int, int]:
    result = run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)])
    width, height = result.stdout.strip().split(",")[:2]
    return int(width), int(height)


def grab_screen_frame(screen_index: int, output: Path, watchdog_seconds: float = 20.0) -> bool:
    """One full-screen frame. avfoundation's first frames can be partial, so keep the 15th.

    Without Screen Recording permission ffmpeg does not fail — it hangs. The watchdog turns that
    hang into a clean False instead of a stuck script."""
    output.unlink(missing_ok=True)
    process = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "avfoundation", "-capture_cursor", "0",
         "-framerate", "30", "-pixel_format", "uyvy422", "-i", str(screen_index),
         "-frames:v", "15", "-update", "1", str(output)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        process.wait(timeout=watchdog_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        return False
    return output.exists() and output.stat().st_size > 0


# --- window geometry ------------------------------------------------------------------------------

def ensure_window_helper() -> Path:
    """Compiles window_rect.swift once into the cache and recompiles only when the source changes."""
    source = SCRIPTS_DIR / "window_rect.swift"
    binary = CACHE_DIR / "window_rect"
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["xcrun", "swiftc", "-O", str(source), "-o", str(binary)], capture_output=True, text=True)
    if result.returncode != 0:
        die(f"could not compile window_rect.swift (is Xcode or the Command Line Tools installed?)\n{result.stderr[-2000:]}")
    return binary


def window_info(pid: int) -> dict:
    result = run([str(ensure_window_helper()), str(pid)])
    return json.loads(result.stdout)


def main_display(info: dict) -> dict:
    for display in info["displays"]:
        if display["main"]:
            return display
    return info["displays"][0]


# --- Unreal ---------------------------------------------------------------------------------------

def resolve_engine_root(uproject: Path, override: str | None) -> Path:
    if override:
        root = Path(override).expanduser()
    else:
        association = json.loads(uproject.read_text(encoding="utf-8")).get("EngineAssociation", "")
        if not re.fullmatch(r"\d+\.\d+", association):
            die(f"EngineAssociation '{association}' is not a launcher version (source build?); pass --engine /path/to/UE_root.")
        root = Path(f"/Users/Shared/Epic Games/UE_{association}")
    app = root / "Engine" / "Binaries" / "Mac" / "UnrealEditor.app"
    if not app.exists():
        die(f"no UnrealEditor.app under {root}; pass --engine with the engine root directory.")
    return root


def wait_for_log_marker(log: Path, marker: str, pid_alive, timeout: float) -> float:
    """Blocks until `marker` appears in the engine log. Returns the seconds waited.

    A fixed sleep is wrong in both directions: a warm shader cache loads a map in under a second,
    a cold one can take minutes. The log says when the level is actually up."""
    started = time.time()
    while time.time() - started < timeout:
        if log.exists() and marker in log.read_text(encoding="utf-8", errors="replace"):
            return time.time() - started
        if not pid_alive():
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:] if log.exists() else []
            die("the game exited before the level was ready. Last log lines:\n" + "\n".join(tail))
        time.sleep(1.0)
    die(f"timed out after {timeout:.0f}s waiting for '{marker}' in {log}. First launch of a map can compile "
        f"shaders for minutes; retry with a larger --timeout, or pass --ready-marker if the project logs something else.")
    return 0.0


def pid_alive(pid: int) -> bool:
    return subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode == 0


def say(text: str) -> None:
    """Spoken cues reach the person at the machine even while the game covers the terminal."""
    subprocess.run(["say", text])
