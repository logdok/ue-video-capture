#!/usr/bin/env python3
"""Real-time capture of an Unreal Engine game window, with game audio, in two phases.

    record.py launch --project P.uproject --map /Game/Maps/Arena [--res 1920x1080]
        Starts the game, waits for the level, measures the window, writes check.png.
        The game keeps running.

    record.py take --session <dir> [--duration 30]
        Voice countdown, records screen + audio, closes the game. Writes raw.mov.

    record.py stop --session <dir>
        Closes the game without recording.

The split is deliberate. Look at check.png between the two phases: a missing UI, a wrong crop or
a black frame found *after* a take has already cost the person at the machine their camera work.
Then run finalize.py on the session.
"""

from __future__ import annotations

import argparse
import json
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uevc_common as c  # noqa: E402

DEFAULT_ANNOUNCE = "Get ready. Keep the mouse over the game window. Countdown in a few seconds."


# --- session file ---------------------------------------------------------------------------------

def session_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path / "session.json" if path.is_dir() else path


def load_session(value: str) -> tuple[Path, dict]:
    path = session_path(value)
    if not path.exists():
        c.die(f"no session at {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def save_session(path: Path, session: dict) -> None:
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


# --- launch ---------------------------------------------------------------------------------------

def parse_res(value: str) -> tuple[int, int]:
    try:
        width, height = value.lower().split("x")
        return int(width), int(height)
    except ValueError:
        c.die(f"--res must look like 1920x1080, got '{value}'")
    return 0, 0


def find_pid_by_token(token: str, timeout: float) -> int:
    """`open` does not report the PID of what it launched. The unique token in -abslog does."""
    started = time.time()
    while time.time() - started < timeout:
        result = subprocess.run(["pgrep", "-f", token], capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.split()]
        if pids:
            return pids[0]
        time.sleep(0.5)
    c.die(f"the game process did not appear within {timeout:.0f}s")
    return 0


def display_containing(info: dict, window: dict) -> dict:
    cx, cy = window["x"] + window["w"] / 2, window["y"] + window["h"] / 2
    for display in info["displays"]:
        if display["x"] <= cx < display["x"] + display["w"] and display["y"] <= cy < display["y"] + display["h"]:
            return display
    return c.main_display(info)


def cmd_launch(args: argparse.Namespace) -> None:
    c.require_macos()
    for tool in ("ffmpeg", "ffprobe", "pgrep", "open"):
        c.require_tool(tool)

    uproject = Path(args.project).expanduser().resolve()
    if not uproject.exists() or uproject.suffix != ".uproject":
        c.die(f"--project must point at a .uproject file, got {uproject}")
    engine = c.resolve_engine_root(uproject, args.engine)
    res_w, res_h = parse_res(args.res)

    name = args.name or args.map.rstrip("/").split("/")[-1].split(".")[0]
    out_root = Path(args.out_dir).expanduser() if args.out_dir else uproject.parent / "Saved" / "VideoCaptures"
    session_dir = out_root / f"{name}-{datetime.now():%Y%m%d-%H%M%S}"
    session_dir.mkdir(parents=True, exist_ok=True)

    token = f"uevc-{secrets.token_hex(4)}"
    log = session_dir / f"game-{token}.log"

    displays = c.window_info(0)
    main = c.main_display(displays)
    cmd = ["open", "-n", "-a", str(engine / "Engine/Binaries/Mac/UnrealEditor.app"), "--args",
           str(uproject), args.map, "-game", f"-ResX={res_w}", f"-ResY={res_h}"]
    if args.fullscreen:
        # Measured on UE 5.8: fullscreen on Mac renders at the display's native resolution and
        # ignores -ResX/-ResY, so a HUD laid out in pixels will not match a windowed capture.
        cmd.append("-fullscreen")
    else:
        if res_w > main["w"] or res_h + 80 > main["h"]:
            c.warn(f"{res_w}x{res_h} plus a title bar may not fit the main display ({main['w']}x{main['h']} pt). "
                   f"Continuing; the window is measured after launch and rejected if it is cut off.")
        win_x = max(0, (main["w"] - res_w) // 2)
        win_y = max(40, (main["h"] - res_h) // 2 + 16)
        cmd += ["-windowed", f"-WinX={win_x}", f"-WinY={win_y}"]
    cmd += ["-nosplash", "-unattended", f"-abslog={log}"] + list(args.extra_arg or [])

    # `open -n` rather than the bare binary: macOS 14+ refuses to activate an app started by a
    # background terminal process, and an inactive app receives mouse wheel and right-drag events
    # but no keyboard input at all. Launching through LaunchServices makes the game the active app.
    # `-n` matters too — without it `open` just brings an already running editor to the front.
    c.run(cmd)
    pid = find_pid_by_token(token, 60.0)
    print(f"launched pid {pid}; waiting for '{args.ready_marker}' (a first launch can compile shaders for minutes)")

    waited = c.wait_for_log_marker(log, args.ready_marker, lambda: c.pid_alive(pid), args.timeout)
    print(f"level ready after {waited:.0f}s; settling {args.settle:g}s")
    time.sleep(args.settle)

    info = c.window_info(pid)
    windows = [w for w in info["windows"] if w["layer"] == 0] or info["windows"]
    if not windows:
        c.die("the game has no on-screen window (minimised, on another Space, or closed?)")
    window = max(windows, key=lambda w: w["w"] * w["h"])
    display = display_containing(info, window)
    if not display["main"]:
        c.warn(f"the window is on '{display['name']}', not the main display. avfoundation screen numbering is "
               f"not guaranteed to follow display order — verify check.png, or pass --screen.")

    devices = c.list_avfoundation_devices()
    screen_index = c.find_screen_index(devices, args.screen)
    if screen_index is None:
        c.die(f"no avfoundation 'Capture screen {args.screen}' device")

    full = session_dir / "check_full.png"
    if not c.grab_screen_frame(screen_index, full):
        c.die("could not capture the screen — Screen & System Audio Recording permission is missing for this terminal (run preflight.py)")
    frame_w, frame_h = c.image_size(full)
    scale = frame_w / display["w"]

    if args.fullscreen:
        content = {"x": 0, "y": 0, "w": frame_w, "h": frame_h}
        titlebar = 0.0
    else:
        # UE may size the client area in points or in pixels; decide from what was measured rather
        # than assuming. Only the scale-1.0 case has been verified on real hardware.
        if abs(window["w"] - res_w) <= 1:
            content_w_pt, content_h_pt = res_w, res_h
        elif abs(window["w"] * scale - res_w) <= 2:
            content_w_pt, content_h_pt = res_w / scale, res_h / scale
        else:
            c.warn(f"window is {window['w']}x{window['h']} pt, which matches {res_w}x{res_h} neither in points nor in "
                   f"pixels (scale {scale:g}); assuming the client area spans the window width")
            content_w_pt, content_h_pt = window["w"], window["w"] * res_h / res_w
        titlebar = window["h"] - content_h_pt
        if not 0 <= titlebar <= 80:
            c.warn(f"derived title bar height {titlebar:g} pt looks wrong; check check.png edges carefully")
        content = {
            "x": round((window["x"] - display["x"]) * scale),
            "y": round((window["y"] - display["y"] + titlebar) * scale),
            "w": round(content_w_pt * scale),
            "h": round(content_h_pt * scale),
        }

    if content["x"] < 0 or content["y"] < 0 or content["x"] + content["w"] > frame_w or content["y"] + content["h"] > frame_h:
        close_game(pid)
        c.die(f"the game's client area {content} extends past the captured screen {frame_w}x{frame_h}. "
              f"Use a smaller --res (finalize.py scales to the requested size), --fullscreen, or a different display scaling.")

    check = session_dir / "check.png"
    c.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-i", str(full),
           "-vf", f"crop={content['w']}:{content['h']}:{content['x']}:{content['y']}", str(check)])

    session = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "project": str(uproject), "map": args.map, "engine": str(engine), "name": name,
        "pid": pid, "log": str(log), "res": [res_w, res_h], "fps": args.fps,
        "fullscreen": bool(args.fullscreen), "screen": args.screen, "scale": scale,
        "window_pt": window, "titlebar_pt": titlebar, "content_px": content,
        "capture_frame_px": [frame_w, frame_h], "active_app": info.get("active", False),
        "check_png": str(check),
    }
    session_json = session_dir / "session.json"
    save_session(session_json, session)

    print(f"session: {session_dir}")
    print(f"client area in capture pixels: {content['w']}x{content['h']} at ({content['x']}, {content['y']}), scale {scale:g}")
    if not info.get("active", False):
        c.warn("the game is not the active app, so its hotkeys will not respond. Ask the person to click inside the game window once.")
    print(f"NEXT: inspect {check} — scene rendered, HUD and UI present, no title bar or desktop at the edges.")
    print(f"      then: record.py take --session '{session_dir}' --duration <seconds>")


# --- take -----------------------------------------------------------------------------------------

def stop_ffmpeg(process: subprocess.Popen) -> None:
    """'q' on stdin makes ffmpeg finalise the file properly; a kill can leave the moov atom unwritten."""
    try:
        process.stdin.write(b"q")
        process.stdin.flush()
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    try:
        process.wait(timeout=20)
        return
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def close_game(pid: int) -> None:
    if not c.pid_alive(pid):
        return
    subprocess.run(["kill", str(pid)])
    for _ in range(40):
        if not c.pid_alive(pid):
            return
        time.sleep(0.5)
    subprocess.run(["kill", "-9", str(pid)])


def last_progress_line(log: Path) -> str:
    text = log.read_text(encoding="utf-8", errors="replace").replace("\r", "\n") if log.exists() else ""
    lines = [line for line in text.splitlines() if line.startswith("frame=")]
    return lines[-1].strip() if lines else ""


def cmd_take(args: argparse.Namespace) -> None:
    c.require_macos()
    path, session = load_session(args.session)
    session_dir = path.parent
    pid = session["pid"]
    if not c.pid_alive(pid):
        c.die("the game from this session is no longer running — run `record.py launch` again")

    devices = c.list_avfoundation_devices()
    screen_index = c.find_screen_index(devices, session.get("screen", 0))
    if screen_index is None:
        c.die("the screen capture device disappeared")
    audio_index = None
    if not args.no_audio:
        audio_index = c.find_audio_index(devices, "BlackHole")
        if audio_index is None:
            c.die("BlackHole is not available, so there is no way to capture game audio. Run preflight.py, "
                  "or record silently with --no-audio.")

    encoders = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    if "h264_videotoolbox" in encoders:
        video_codec = ["-c:v", "h264_videotoolbox", "-b:v", args.video_bitrate]
    else:
        video_codec = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16"]

    raw = session_dir / "raw.mov"
    ffmpeg_log = session_dir / "raw.ffmpeg.log"
    source = f"{screen_index}:{audio_index}" if audio_index is not None else str(screen_index)
    cmd = ["ffmpeg", "-hide_banner", "-y", "-thread_queue_size", "1024", "-f", "avfoundation",
           "-capture_cursor", "0", "-framerate", str(session["fps"]), "-pixel_format", "uyvy422",
           "-i", source] + video_codec
    cmd += ["-c:a", "aac", "-b:a", "256k"] if audio_index is not None else ["-an"]
    cmd.append(str(raw))

    if not args.no_countdown:
        c.say(args.announce)

    with ffmpeg_log.open("w", encoding="utf-8") as log_handle:
        started = time.time()
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_handle)

        # Screen + audio devices take a moment to start delivering; anything in the first second or
        # two is unreliable, which is exactly why the countdown lives in this pre-roll and is cut later.
        time.sleep(args.preroll)
        if process.poll() is not None:
            log_handle.flush()
            c.die("ffmpeg stopped immediately:\n" + ffmpeg_log.read_text(encoding="utf-8", errors="replace")[-2000:])
        if not args.no_countdown:
            c.say("three. two. one. go.")
        go_offset = time.time() - started
        print(f"recording — go at {go_offset:.2f}s after capture start; {args.duration:g}s to go")

        time.sleep(args.duration + args.tail)
        stop_ffmpeg(process)

    if not args.no_countdown:
        c.say("done")
    if not args.keep_game_open:
        close_game(pid)

    if not raw.exists() or raw.stat().st_size == 0:
        c.die(f"no recording was written; see {ffmpeg_log}")

    probe = c.ffprobe_json(raw)
    durations = {s["codec_type"]: float(s.get("duration", 0) or 0) for s in probe["streams"]}
    progress = last_progress_line(ffmpeg_log)
    session["take"] = {
        "raw": str(raw), "go_offset": go_offset, "duration": args.duration,
        "audio": audio_index is not None, "video_duration": durations.get("video"),
        "audio_duration": durations.get("audio"), "ffmpeg_progress": progress,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_session(path, session)

    print(f"raw: {raw}  video {durations.get('video', 0):.2f}s  audio {durations.get('audio', 0):.2f}s")
    print(f"ffmpeg: {progress}")
    if "drop=" in progress and not progress.split("drop=")[1].strip().startswith("0"):
        c.warn("ffmpeg reports dropped frames — the machine could not keep up; consider a smaller --res")
    print(f"NEXT: finalize.py --session '{session_dir}'")


def cmd_stop(args: argparse.Namespace) -> None:
    _, session = load_session(args.session)
    close_game(session["pid"])
    print(f"closed pid {session['pid']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch", help="start the game and measure its window")
    launch.add_argument("--project", required=True, help="path to the .uproject")
    launch.add_argument("--map", required=True, help="map package path, e.g. /Game/Maps/Arena or /MyPlugin/Demo/Maps/Demo")
    launch.add_argument("--engine", help="engine root; default /Users/Shared/Epic Games/UE_<EngineAssociation>")
    launch.add_argument("--res", default="1920x1080", help="game client area, e.g. 1920x1080")
    launch.add_argument("--fps", type=int, default=30, help="capture frame rate (30 is the verified value)")
    launch.add_argument("--name", help="base name for the session and output files")
    launch.add_argument("--out-dir", help="default <project>/Saved/VideoCaptures")
    launch.add_argument("--ready-marker", default="Load map complete",
                        help="engine log substring meaning the scene is ready; use a project-specific one if the UI appears later")
    launch.add_argument("--settle", type=float, default=3.0, help="seconds to wait after the marker")
    launch.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the marker")
    launch.add_argument("--screen", type=int, default=0, help="avfoundation 'Capture screen N' to record")
    launch.add_argument("--fullscreen", action="store_true", help="native-resolution fullscreen instead of a window")
    launch.add_argument("--extra-arg", action="append", help="extra engine argument, repeatable (e.g. --extra-arg=-ExecCmds=\"stat fps\")")
    launch.set_defaults(func=cmd_launch)

    take = sub.add_parser("take", help="record the running game")
    take.add_argument("--session", required=True, help="session directory or session.json")
    take.add_argument("--duration", type=float, default=30.0, help="usable seconds after 'go'")
    take.add_argument("--no-audio", action="store_true", help="record without BlackHole audio")
    take.add_argument("--no-countdown", action="store_true", help="skip the spoken cues")
    take.add_argument("--announce", default=DEFAULT_ANNOUNCE, help="spoken before capture starts (not recorded)")
    take.add_argument("--preroll", type=float, default=2.0, help="seconds of capture before the countdown")
    take.add_argument("--tail", type=float, default=1.5, help="extra seconds after the duration")
    take.add_argument("--video-bitrate", default="40M", help="intermediate bitrate for the raw capture")
    take.add_argument("--keep-game-open", action="store_true", help="do not close the game afterwards")
    take.set_defaults(func=cmd_take)

    stop = sub.add_parser("stop", help="close the game of a session")
    stop.add_argument("--session", required=True)
    stop.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
