# Plan

Status: **2026-11-20.** Scaffold written, nothing run against hardware. No Pi and no robot in
hand yet.

Read this first if you are picking the project up. It records *why* things are the way they are,
so decisions already settled don't get reopened. The parent project is
`../Diectic-VLA-FYP` and paths below that start `../Diectic-VLA-FYP/` point into it.

## What this is

A demo that runs on a Raspberry Pi 5 with a Unitree Go2 Air, needing **no router and no
internet**. The robot broadcasts its own Wi-Fi; the Pi joins it; the laptop joins it too. Press a
button on the laptop and Roxy speaks. Later, a hand gesture triggers the same actions.

```
laptop browser  --HTTP/WS--> |      | --WebRTC--> Go2 Air      robot's own AP
laptop mic      --WS------>  |  Pi  | --aplay-->  speaker      no router, no internet
                             |      | <--USB----  Orbbec 335   (stage 3)
```

The point is an architecture test: the Pi becomes the middleman instead of the laptop. The
parent project has the laptop in the middle, running ROS over DDS to three machines. Here the Pi
owns the robot link, the camera and the speech, and the laptop only triggers things.

## Status

| | |
|---|---|
| `pi/server.py` | written, **never run** |
| `pi/speech.py` | copied verbatim from the parent repo, works there |
| `pi/voices.py` | copied verbatim, imports clean, 102 voices resolve |
| `pi/audio.py` | `boost()` copied from the parent repo; the `aplay` path is untested |
| `pi/robot.py` | written from the library's own examples, **never run** |
| `pi/commands.py` | written and unit-checked by hand (matching verified) |
| `pi/static/index.html` | written, **never loaded in a browser** |
| `laptop/listen.py` | written, wake filter verified; the Moonshine path is untested |
| `pi/camera.py` | **stub**, stage 3 |
| `pi/gestures.py` | ported from `../hand-gesture-recognition-mediapipe` (20-class retrain of 2026-11-20); ~21 fps live on the Pi; sign -> intent mapping still empty |

No Python dependency has been installed for this project. Nothing has touched a Pi, a robot, a
speaker or a mic.

## Do this next, without any hardware

Most of the work doesn't need the Pi or the dog. **Piper, sox, MediaPipe and OpenCV all run on
the laptop**, which is x86 Linux, and `pi/server.py` has no Pi-specific code in it.

1. **Run stage 1 entirely on the laptop.** Make a venv, install `requirements-pi.txt`, download
   one Piper voice, set `ALSA_DEVICE=default` in `.env`, start `pi/server.py`, open
   `http://localhost:8080` and press the button. This exercises the server, the WebSocket, the
   page, Piper, sox and the voice library — everything except the robot and the Pi's sound card.
   Expect to find bugs here; none of this has executed.
2. **Then the mic half, also on the laptop.** `scripts/laptop-setup.sh`, then `laptop/listen.py`
   with `PI_URL=ws://localhost:8080/ws`. Both halves on one machine, talking over localhost.
   This gets the Moonshine model downloaded while you still have internet, which you will want
   done before the demo anyway.
3. **Stage 3 against the laptop's webcam.** `camera.py` wants `cv2.VideoCapture` on a V4L2
   index; the laptop's built-in camera is index 0. Nothing about it is Orbbec-specific until you
   want depth.
4. **Stage 4 against the laptop's webcam too.** See below — most of it is already written in the
   parent repo and needs lifting, not writing.

That leaves only stage 2 (the robot link) genuinely blocked on hardware, plus the Pi-specific
parts: ALSA device names, arm64 wheel availability, and performance.

## Decisions already made, and why

**No ROS, no Docker.** The parent project needs ROS because it moves camera frames and odometry
between three machines. This has one machine and a button. Plain Python is faster to debug over
ssh and avoids an arm64 Docker build on a Pi.

**One asyncio loop, aiohttp.** It serves the page, the WebSocket and (stage 3) the MJPEG stream
from one process. Synthesis blocks, so it runs in a thread; playback has its own queue in
`audio.py`.

**The button, the mic and the gestures all produce the same intent names** and converge on
`Demo.do_intent` in `server.py`. One code path, so a new trigger is a few lines.

**The mic lives on the laptop.** The Bluetooth mic can't pair to the Pi, but this is the better
split regardless: only text crosses the Wi-Fi (~50 bytes a sentence against ~32 KB/s for 16 kHz
audio), Moonshine is CPU-bound and the Pi will be busy with camera frames, and Bluetooth audio
on a Pi is a known misery (BlueALSA/PipeWire, HFP, pairings that don't survive reboots).

**The laptop decides *whether* a sentence was meant for us; the Pi decides *what it means*.**
Filtering on the laptop keeps room chatter off the network; the action table stays next to the
robot code, in one file.

**The Orbbec is the camera, not the Go2's own.** The Go2 sends H.264 over WebRTC and **the Pi 5
has no hardware H.264 decoder** (HEVC only), so aiortc/PyAV would decode in software while
MediaPipe also wants CPU. The Orbbec is MJPEG over UVC, far cheaper, and it is the camera that
actually sees the person pointing. Only take the H.264 path if the demo needs the robot's-eye
view, and expect to drop resolution and frame rate.

**The speaker is on the Pi.** See "Unverified" below — this is not a free choice yet.

**`LocalAP`, never `Remote`.** `Remote` goes through Unitree's cloud with an email and password,
which defeats the whole point. `LocalSTA` with `GO2_IP` is kept for development on a router.

## Stages

### Stage 1 — button to speech. *Code written, never run.*

Laptop button → Pi → Piper → speaker. No robot needed, and `server.py` deliberately never
blocks on the robot connection, so this demos with Roxy switched off.

Do it on the laptop first (above). On the Pi afterwards: `scripts/setup-pi.sh`, then `aplay -L`
to find the card and put it in `.env` as `ALSA_DEVICE`. `plughw:...` rather than `hw:...` so
ALSA converts the clip's rate and channels to whatever the card accepts.

### Stage 2 — the robot link. *Code written, never run. Needs the robot.*

`pi/robot.py` connects with `WebRTCConnectionMethod.LocalAP` plus the AES key, switches the
robot into `normal` motion mode, and sends sport-mode actions by name.

- Firmware ≥ 1.1.15 needs the per-device AES-128 key. Copy `GO2_AES_KEY` from the parent repo's
  `.env`, or fetch it with
  `../../webrtc/unitree_webrtc_connect/examples/fetch_aes_key.py` while on the same network.
- **Sport commands are silently ignored in `ai` motion mode.** `connect()` switches to `normal`
  and waits 5 s for the robot to stand. This is an easy hour to lose.
- `move(x, y, z)` is a **single shot**, not a stream. The Go2 keeps walking until the next
  command, so anything driving it continuously has to keep sending them (or send `stop`).
  Relevant the moment gestures drive it.
- Command IDs came from `../../webrtc/unitree_webrtc_connect/unitree_webrtc_connect/constants.py`
  (`SPORT_CMD`, around line 123) and the shape of a request from
  `examples/go2/data_channel/sportmode/`.

First thing to run once the robot is on, because it settles an open question in thirty seconds:

```python
from unitree_webrtc_connect.webrtc_audiohub import WebRTCAudioHub
print(await WebRTCAudioHub(conn).get_audio_list())
```

### Stage 3 — the camera. *Stub.*

Orbbec Gemini 335 colour frames on the Pi, and an MJPEG endpoint so the laptop browser can see
what the Pi sees (aiohttp `StreamResponse`, multipart/x-mixed-replace).

- Colour over plain UVC should need no Orbbec SDK — `cv2.VideoCapture` with the MJPEG fourcc.
  Only depth would need `pyorbbecsdk`, and nothing here wants depth.
- Keep **one** frame in a one-slot buffer filled by a grabber thread, so a slow consumer drops
  frames instead of falling behind. A queue here means growing latency.
- The parent repo's `scripts/orbbec_view.py` already serves an MJPEG preview with the camera's
  FOV drawn on — read it before writing this.
- **Found on the Pi (2026-11-21):** colour is `/dev/video6` (MJPG/YUYV, 640x480); 0 is depth,
  2 and 4 are IR. The number can move, so use
  `/dev/v4l/by-id/usb-Orbbec_R__Orbbec_Gemini_335_CP06563000FH-video-index0`.
- **The FYP's `deictic-pi-1` container owns the camera** (ROS 2 orbbec node, starts at boot,
  `/orbbec/color/image_raw` on domain 30). Stop it first: `sudo docker stop deictic-pi-1`. After it
  stops, the colour interface is left without a driver (only depth and IR show up) until the
  camera is replugged, or re-attached in software:
  `echo 0 | sudo tee /sys/bus/usb/devices/5-1/authorized; echo 1 | sudo tee /sys/bus/usb/devices/5-1/authorized`

### Stage 4 — hand pose. *Stub. Mostly already written — lift, don't write.*

**Do not start from `../../hand-gesture-recognition-mediapipe`.** The parent repo already has a
working, documented node that does this:
`../Diectic-VLA-FYP/ros_ws/src/go2_gesture/scripts/gesture_teleop.py`, 592 lines, with the three
model files it actually needs already trimmed out into
`../Diectic-VLA-FYP/ros_ws/src/go2_gesture/model/` (`hand_landmarker.task`,
`keypoint_classifier.tflite`, `keypoint_classifier_label.csv`).

Most of those 592 lines are ROS plumbing you don't want. The parts to lift:

| in `gesture_teleop.py` | what it is |
|---|---|
| `_KeyPointClassifier` (l. 99) | the TFLite MLP wrapper, on `ai-edge-litert` |
| `_landmark_list` / `_preprocess` (l. 117, 125) | landmarks → the classifier's input vector |
| `_finger_sign` (l. 137) | which fingers are straight, by rules |
| `_is_heart` (l. 163) | the two-hand sign |
| `GESTURE_CMD` (l. 77) | sign → velocity; map these onto `commands.py` intents instead |
| `update_gesture` / `command` (l. 406, 411) | the smoothing window |

Two hard-won details recorded in its docstring, worth not rediscovering:

- **The classifier gets thumbs-up wrong.** On MediaPipe's own sample photos it called a thumbs up
  "call" and open hands "dislike" at 0.99 confidence. The finger *rules* got all of them right,
  which is why the trick mode uses rules and not the classifier.
- **A sign must be held** for most of a history window before it fires, and fires again only
  after the hand is lowered and a cooldown passes. Without this, a hand passing through a pose on
  its way somewhere else triggers it.
- `mirror: true` in its config flips the frame horizontally so left/right match yours when you
  are facing the dog. You will want the same.

The parent repo installs this as `mediapipe` + `ai-edge-litert` (a much smaller runtime than
full TensorFlow — `../Diectic-VLA-FYP/docker/ros/Dockerfile`, lines 73-76). Use the same on the
Pi; `requirements-pi-vision.txt` already lists it.

## Unverified — do not repeat these as fact

**Whether the Go2 Air has a usable speaker.** The Pro and EDU have a 3W speaker in the head, per
reseller spec listings (not Unitree's own sheet). The Air is not covered by any source found.
Separately, several developers report the Go2's speaker is **not open to custom code even on the
models that have it**, and at least one group exhibiting a Go2 used external speakers instead.
`WebRTCAudioHub` exposes `upload_audio_file` / `play_by_uuid`, which is where the idea came
from — but that is the library's protocol support for the Go2 family, not proof about this
variant's hardware. The parent project bolting a USB sound card and a 3.5 mm speaker onto the Pi
is itself evidence that this was tried and didn't work.

Settle it with the `get_audio_list()` call in stage 2. **Nothing in this project depends on the
answer** — the Pi speaker is the design, and the robot's own speaker would only ever be a bonus.

**Which band the Go2's AP uses.** If it is 2.4 GHz, the Bluetooth mic and the laptop's link to
the Pi compete on the same radio. Combo chips time-slice and it usually works, but expect
occasional audio dropouts under load. If the AP is 5 GHz this is a non-issue. Check when you
have the robot.

**Which Bluetooth profile the mic negotiates.** Recording uses HFP, not A2DP (A2DP is
playback-only). HFP gives either 8 kHz CVSD, which will noticeably hurt transcription, or 16 kHz
mSBC, which is fine. `pactl list sources | grep -A3 'Name.*bluez'`.

**arm64 wheels.** `piper-tts`, `mediapipe` and `ai-edge-litert` all need to install on the Pi's
Python. The parent project installs all three in an arm64 container, so they exist, but not for
this exact Python version necessarily.

## The network

The Go2 in AP mode is always `192.168.12.1` and runs DHCP. The Pi has one Wi-Fi radio, so when
`wlan0` is on the robot's AP the Pi has no other network.

- **development**: Pi `wlan0` on your router; `apt` and `pip` work.
- **demo**: Pi `wlan0` and the laptop both on the robot's AP. Nothing else needed.
- If you ever need both at once: Pi `wlan0` → robot AP, Pi `eth0` → router. One cable.

Give the Pi a **static address** on `192.168.12.0/24`, or set up avahi. On DHCP the Pi moves and
the laptop can't find it; `PI_URL` in `.env` has to point somewhere stable.

## Not decided

- **How the gesture and the voice interact.** Right now they are independent triggers of the same
  intents. If a gesture should qualify a spoken word ("go *there*"), that is the parent project's
  whole research question and does not belong in this demo.
- **Whether the Pi should drive the robot continuously** from gestures, which needs a command
  loop rather than one-shot `move()` calls, plus a deadman timeout for when the hand leaves the
  frame. `gesture_teleop.py` solves exactly this with `no_hand_timeout` — copy its approach.
- **The Velodyne lidar**, mentioned as a later addition. Nothing here anticipates it.

## Reference

```sh
# the Pi (or the laptop, for now)
bash scripts/setup-pi.sh                  # VISION=1 also installs OpenCV + MediaPipe
./venv/bin/python pi/server.py
curl -X POST localhost:8080/say -d 'hello' # speak without a browser

# the laptop
bash scripts/laptop-setup.sh
./venv-laptop/bin/python laptop/listen.py
./venv-laptop/bin/python laptop/listen.py --no-wake   # quiet room: send everything
python3 -m sounddevice                                # list mics, for MIC_DEVICE
```

`.env` keys are documented in `.env.example`. `VOICE` is any of the 103 names in `pi/voices.py`
(`scooby`, `robot`, or `<speaker>-<style>`), changeable live from the page's dropdown; `clear` is
the most intelligible style and all 100 generated voices have `punch` on for a noisy room.
