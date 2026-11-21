# Plan

Status: **2026-11-21.** Everything below runs on the real hardware (Pi 5, Go2 Air, Orbbec Gemini
335, USB speaker) and was shown at the Atlas promotional event. **Next: test the DJI mic on the
other laptop** (it has USB-C; this laptop's 3.5 mm jack can't take the DJI receiver).

Read this first if you are picking the project up. It records *why* things are the way they are,
so decisions already settled don't get reopened. The parent project is
`../fyp/Diectic-VLA-FYP`; paths below that start `FYP/` point into it.

## Pick up here: the mic on the other laptop

Only the laptop half moves; the Pi keeps its own copy and its own `.env`.

1. `git clone git@github.com:silveradium/roxy-ignite-demo.git && cd roxy-ignite-demo`
2. `bash scripts/laptop-setup.sh` (apt + `venv-laptop`). If pip fails on numpy, that machine has
   Python 3.13: delete the `numpy<2` line from `requirements-laptop.txt` (only the Pi needs it).
3. Plug the DJI receiver into **USB-C**. Settings → Sound → Input → the DJI, and keep
   `MIC_DEVICE=default`; or `./venv-laptop/bin/python -m sounddevice` and set `MIC_DEVICE=DJI`
   (any substring of its name).
4. `cp .env.example .env`, then set `PI_URL=ws://<pi>:8080/ws` (`getent hosts raspberrypi.local`).
   The laptop needs no keys: Gemini and the robot are the Pi's business.
5. First run with internet: Moonshine downloads ~260 MB to `~/.cache/moonshine_voice/` (or copy
   that folder over from this laptop).
6. `./venv-laptop/bin/python laptop/listen.py --dry-run` to see what it hears without moving the
   dog, then without `--dry-run`. Watch the page's **heard** panel (`http://<pi>:8080`).
7. **Only one listener at a time**, or every command is sent twice. Stop the one on this laptop.

What to check: does the DJI fix the name ("Roxy" came out as Roxas / Proxy / Oxy / rocksy on
the built-in mic at the venue; `NAMES`/`LOOSE` in `laptop/listen.py` accept those)? Add any new
mishearing there.

## What this is

```
LAPTOP                                PI 5 (pi/server.py, one asyncio process)          OUT
mic ─ Moonshine ─ wake filter ─WS──▶  commands.match() ─▶ robot.act() ─WebRTC─────────▶ Go2
                                      dialog.py ─ Gemini ─▶ words ─▶ Piper ─▶ aplay ───▶ USB speaker
browser (buttons, heard panel) ─WS─▶  do_intent()
                                      gestures.py ◀─ Orbbec (USB, MJPEG)
```

- **Keywords decide every action** (`commands.py`), at once. **Gemini only writes the words**
  for voice (`dialog.py`), told which action is happening; no keyword = conversation. "stop",
  buttons and gestures keep fixed lines. No key / no internet / an error: fixed lines.
- The laptop decides *whether* a sentence was for Roxy (wake word); the Pi decides *what it
  means*. Sentences without the wake word still reach the page (display only).
- Sentences heard while Roxy is speaking (or < 2 s after) are dropped, except stop, so the mic
  never answers her own voice.

## Status

| | |
|---|---|
| `pi/server.py` | runs on the Pi; robot reconnects every 5 s; commands run concurrently so "stop" never waits behind a dance |
| `pi/robot.py` | WebRTC `LocalSTA` to the Go2 (firmware in **MCF** mode); tricks + timed turns/walks; logs refusals |
| `pi/commands.py` | keyword matcher, tested on ~50 natural sentences |
| `pi/dialog.py` + `pi/events/atlas.txt` | Gemini replies with the Atlas event persona; tested live (~0.7–1 s) |
| `pi/speech.py`, `pi/voices.py`, `pi/audio.py` | Piper + sox on the Pi; default voice `hazel-clear`; also `kid` (pitch + formant via pyworld) |
| `pi/gestures.py` | heart / hello signs, fingertip circles → spins; ~21 fps on the Pi |
| `pi/static/index.html` | buttons, voice picker, heard panel with Roxy's reply, log |
| `laptop/listen.py` | Moonshine medium-streaming (the largest), `--dry-run`, resends after a reconnect |
| `pi/camera.py` | still a stub: gestures open the camera themselves; no MJPEG preview yet |

## Known problems

**Power (the big one).** The Pi's supply negotiates 3 A, so the Pi 5 allows only **600 mA** for
all USB ports together. Camera + speaker go over it: the Pi cuts every USB port for a moment
(`over-current change` in `journalctl -k`), a reply goes silent and the camera drops. Twice the
camera then stuck in a reset loop (USB disconnect every 3–4 s) and once it stopped answering
until physically replugged. **Fix: the official 27 W (5 A) supply, or a powered USB hub for the
camera and speaker.** Not `usb_max_current_enable=1` on the 3 A supply: the Pi itself may brown
out. Until then, `GESTURES=0` in the Pi's `.env` takes the camera out of the picture.

**The Pi dropped off the network twice** under load (ping answered, ssh and the page didn't);
probably the same power problem. Cause not confirmed.

**Gesture false triggers.** The `hello` class (19) fires on ordinary open hands; palm and stop
were mapped to the wave and removed again for that reason. Not yet done: a confidence floor
(~0.9) and a longer hold (~1 s, 80% of 20 frames) in `gestures.Trigger`.

**The dog refuses tricks unless standing** (code -1 / 401001). The page log says "refused by the
robot ... is it standing?". Say "Roxy, stand up" first. The Unitree app being connected also
blocks the Pi (one WebRTC client at a time).

**The laptop ↔ Pi WebSocket** occasionally timed out while the Pi was busy (camera + hand
tracking + voice at ~120% CPU); `listen.py` now waits 60 s for a ping and resends after a
reconnect.

## Decisions already made, and why

**No ROS, no Docker.** One machine and a button. Plain Python is faster to debug over ssh.

**One asyncio loop, aiohttp.** Page, WebSocket and HTTP from one process. Synthesis and Gemini
run in threads; playback has its own queue in `audio.py`; the camera loop is a thread.

**Button, mic and gestures produce the same intent names** and converge on `Demo.do_intent`.

**The mic lives on the laptop.** Only text crosses the Wi-Fi; Moonshine is CPU-bound and the
Pi is busy with the camera.

**Keywords, not Gemini, choose actions.** Deterministic, instant, and works with no internet.
Gemini can't make the dog do something nobody asked for.

**Walking needs a clear phrase.** No bare "left", "right", "back", "come": "you're so right" used
to turn the dog. Anything that isn't walking beats walking in a sentence ("come here and sit"
sits). Turns are timed: `TURN_GAIN=1.35` (1.2 turned ~80°), 0.8 rad/s; "turn around" is 360°.

**The Orbbec is the camera, not the Go2's own.** The Pi 5 has no hardware H.264 decoder.

**`LocalSTA` on a shared router**, not the robot's AP: the venue Wi-Fi gives the Pi internet for
Gemini. `LocalAP` still works (`GO2_IP=192.168.12.1`), but then no Gemini.

## Hard-won details

- **MCF mode.** Firmware ≥ 1.1.7 runs `mcf` and rejects a switch to `normal` (7004). The sport
  IDs we use are the same in both; `robot.py` leaves `mcf` alone.
- **Move is not one-shot.** The Go2 stops shortly after the last Move, so timed motions stream
  Move at 10 Hz, then send zero + StopMove. "stop" cancels them.
- **Camera path.** Don't use `/dev/v4l/by-id/...-index0`: after a USB reset it landed on the depth
  stream. `CAMERA_DEVICE=orbbec` finds the colour stream as UVC interface `…:1.4`, index 0.
- **The FYP's `deictic-pi-1` container owns the camera** and starts at boot unless stopped
  (`sudo docker stop deictic-pi-1`; it then stays stopped across reboots). After stopping it the
  colour interface can be left without a driver until the camera is replugged.
- **Gemini** rejects a client timeout under 10 s (400 INVALID_ARGUMENT); showing it the stock
  reply made it parrot it, so the prompt asks for fresh words.
- **numpy<2 on the Pi** with `opencv-python` and `opencv-contrib-python` both pinned to 4.11
  (mediapipe and unitree_webrtc_connect each pull one in; 5.x needs numpy 2).
- **pyworld and pyaudio** have no arm64 wheels: `setup-pi.sh` installs `build-essential`,
  `python3-dev`, `portaudio19-dev`. pyworld needs `setuptools<81`.
- **Laptop 3.5 mm jack + DJI receiver = no mic.** The receiver's TRS plug puts its audio on the
  headphone contacts of a TRRS combo jack. Use USB or a TRS→TRRS adapter.

## Not decided

- **How gestures and voice interact** beyond being independent triggers.
- **Continuous driving from gestures** (a command loop with a deadman); FYP's
  `go2_gesture/scripts/gesture_teleop.py` has `no_hand_timeout` for this.
- **An MJPEG camera preview** on the page (`camera.py`); FYP's `scripts/orbbec_view.py` has one.

## Reference

```sh
# the Pi (ssh fyp@raspberrypi.local)
cd ~/roxy-pi-demo
pkill -9 -f "^./venv/bin/python"                                   # stop (plain pkill can hang)
nohup ./venv/bin/python -W ignore pi/server.py > ~/roxy-server.log 2>&1 &
tail -f ~/roxy-server.log                                          # "robot connected", "Gemini replies on"
curl -X POST localhost:8080/say -d 'hello'                         # speak without a browser
journalctl -k -f | grep -i -E 'over-current|disconnect'            # USB power trouble

# the laptop
./venv-laptop/bin/python laptop/listen.py            # --dry-run: hear without acting
./venv-laptop/bin/python -m sounddevice              # list mics, for MIC_DEVICE
```

`.env` keys are documented in `.env.example`. The Pi's `.env` holds `GO2_AES_KEY`,
`GEMINI_API_KEY`, `GO2_IP=192.168.0.211`, `VOICE=hazel-clear`, `GESTURES=1`,
`CAMERA_DEVICE=orbbec`, `TURN_GAIN=1.35`, `GEMINI_EVENT=atlas`. Never put keys in `.env.example`.
