# One-time machine setup

Real-time capture with sound needs three things on the Mac. Each one needs the person at the
machine — an administrator password, clicks in a system app, or a privacy toggle — so guide them
through it rather than trying to script it. `preflight.py` names the section that is missing.

UI names below are from an English-language macOS 26. On a localised system they are translated;
describe the position of the control if the names don't match.

## §1 BlackHole — a virtual audio device ffmpeg can record from

macOS cannot capture its own sound output: avfoundation only lists microphones. BlackHole is a
loopback driver that shows up as both an output and an input.

```sh
brew install blackhole-2ch          # asks for the administrator password
```

Then restart CoreAudio — **the step that is easy to miss**. After installation the driver is on
disk at `/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver`, but no app, System Settings included,
sees it until CoreAudio reloads its plug-ins:

```sh
sudo killall coreaudiod             # sound drops for a second, then comes back
```

Suggest it to the user as `! sudo killall coreaudiod` so it runs in this session. Verify:

```sh
ffmpeg -f avfoundation -list_devices true -i ""   # "BlackHole 2ch" should appear under audio devices
```

If it still isn't listed, a reboot will load it.

## §2 Multi-Output Device — hear the game and record it at the same time

A game plays to the system's default output. Pointing that output at BlackHole alone would record
fine but leave the person at the machine hearing nothing. A Multi-Output Device sends the same
audio to the speakers and to BlackHole.

1. Open **Audio MIDI Setup**: `open -a "Audio MIDI Setup"` (it lives in
   `/System/Applications/Utilities`). If a MIDI window opens instead, choose **Window → Show Audio
   Devices**.
2. **+** at the bottom left → **Create Multi-Output Device**.
3. In the panel on the right, tick **Use** for the speakers or headphones the person listens on
   (for example **MacBook Pro Speakers**) and for **BlackHole 2ch**.
4. Set **Primary Device** to those speakers.
5. Tick **Drift Correction** for **BlackHole 2ch**.
6. Right-click **Multi-Output Device** in the list on the left → **Use This Device For Sound Output**.

Warn the user that the volume keys and the volume slider stop working while a Multi-Output Device
is the output — a macOS limitation. Set the listening volume on the real speakers beforehand.

Verify with `preflight.py --audio-test`. It speaks a phrase through the system output while
recording BlackHole alone; a healthy result peaks around −6 dB.

## §3 Screen & System Audio Recording permission

**System Settings → Privacy & Security → Screen & System Audio Recording** → enable the app that
hosts the terminal session (Terminal, iTerm, Ghostty, Visual Studio Code…). To find out which one
this session runs in:

```sh
P=$$; while [ "$P" != 1 ]; do P=$(ps -o ppid= -p $P | tr -d ' '); ps -o comm= -p $P; done | grep -i '\.app' | tail -1
```

macOS may offer to quit and reopen that app. Quitting ends the current Claude Code session, so do
this step last, after anything else in progress.

Without this permission ffmpeg does not report an error — it hangs. The scripts guard against that
with a watchdog, and `preflight.py` reports it as blocking.

## §4 After recording

If the user wants their volume keys back, switch the output to the speakers again:
**System Settings → Sound → Output**. Leaving the Multi-Output Device in place is harmless and saves
redoing §2 next time.
