# roxy-pi-demo

A Unitree Go2 Air demo that runs on a Raspberry Pi 5 and needs no router and no internet: the
robot broadcasts its own Wi-Fi, the Pi joins it, and the laptop joins it too. Press a button on
the laptop (or say something to the mic) and Roxy speaks and moves.

Plain Python, no ROS and no Docker. The parent project
([Diectic-VLA-FYP](../Diectic-VLA-FYP)) uses ROS because it moves camera frames and odometry
between three machines; this one has a single machine and a button, so the speech code is lifted
across as two files and the rest is a small asyncio server.

```
laptop browser  --HTTP/WS--> |      | --WebRTC--> Go2 Air      robot's own AP
laptop mic      --WS------>  |  Pi  | --aplay-->  speaker      no router, no internet
                             |      | <--USB----  Orbbec 335   (stage 3)
```

**Nothing here has been run yet** — there is no Pi and no robot in hand. See
[PLAN.md](PLAN.md) for what is built, what is untested, which decisions are already settled
and why, and what can be done on the laptop alone in the meantime.

## Why the mic is on the laptop

The Bluetooth mic pairs to the laptop, not the Pi. That is also the better split: Moonshine is
CPU-bound and the Pi will be busy with camera frames, and only text crosses the Wi-Fi (~50 bytes
a sentence, against ~32 KB/s for 16 kHz audio) which matters on the Go2's weak AP. The laptop
decides *whether* a sentence was meant for us; the Pi decides *what it means*, next to the robot
code.

## Layout

| | |
|---|---|
| `pi/server.py` | everything on one asyncio loop: WebSocket, HTTP, Piper, the robot |
| `pi/speech.py` | text -> WAV through Piper + sox effects (lifted from the parent repo) |
| `pi/voices.py` | the 103 named voices: `kid`, `scooby`, `robot`, and 20 speakers x 5 robot styles |
| `pi/audio.py` | WAV -> `aplay`, queued on a worker thread, with a soft-limited gain boost |
| `pi/robot.py` | the Go2 over WebRTC in AP mode; sport-mode actions and velocity commands |
| `pi/commands.py` | words -> intent -> robot action + spoken reply. One table to edit |
| `pi/camera.py` | **stage 3**, stub: Orbbec colour frames |
| `pi/gestures.py` | **stage 4**, stub: MediaPipe hand pose -> the same intents |
| `pi/static/index.html` | the button page. No CDNs: there is no internet on the AP |
| `laptop/listen.py` | Moonshine mic -> wake-word filter -> the Pi |

## Setup

On the Pi, **while it still has internet**:

```sh
bash scripts/setup-pi.sh          # VISION=1 also installs OpenCV + MediaPipe
aplay -L                          # pick the card, put it in .env as ALSA_DEVICE
cp .env.example .env              # then fill in GO2_AES_KEY
./venv/bin/python pi/server.py
```

On the laptop, also while it has internet (Moonshine's model downloads on first use):

```sh
bash scripts/laptop-setup.sh
./venv-laptop/bin/python laptop/listen.py
```

Then open `http://<pi>:8080`. The page works on its own without the mic, and the server starts
and speaks even when the robot is absent — the robot connection is attempted in the background
and never blocks startup, so stage 1 demos with the robot switched off.

## The network

The Go2 in AP mode is always `192.168.12.1` and hands out DHCP addresses. The Pi has one Wi-Fi
radio, so when `wlan0` is on the robot's AP it has no other network:

- **development**: Pi `wlan0` on your router, laptop anywhere on it. Internet, easy `apt`.
- **demo**: Pi `wlan0` and the laptop both on the robot's AP. Nothing else needed.

Give the Pi a **static address** on `192.168.12.0/24` (or set up avahi) — otherwise DHCP moves it
and the laptop can't find it. Put it in `.env` as `PI_URL`.

Two things to know if the Bluetooth mic misbehaves: Bluetooth and 2.4 GHz Wi-Fi share the band,
so if the Go2's AP is 2.4 GHz they compete on the laptop's radio; and recording over Bluetooth
uses HFP, which is either 8 kHz CVSD (bad for transcription) or 16 kHz mSBC (fine) — check with
`pactl list sources | grep -A3 'Name.*bluez'`.

## Voices

`VOICE` in `.env` is any name from `pi/voices.py`, or change it live from the page's dropdown.
103 of them: `kid`, `scooby`, `robot`, and `<speaker>-<style>` over 20 Piper speakers (ryan, amy,
lessac, kristin, hazel, jenny, prudence, spike, obadiah, poppy, alan, rex, sally, clara, lena,
ben, isla, fiona, hamish, owen) and 5 styles (`clear`, `bright`, `metal`, `pixel`, `droid`).

`clear` is the most intelligible; all 100 have `punch` on and a lifted pitch, tuned for a noisy
hall. Every effect is a key in a spec dict — see the docstring in `pi/speech.py`.

`hazel-clear` is the default (VOICE in .env). `kid` has no robot effects: LibriTTS speaker 351, sped up, then pitch +4.5 and
formants +2 semitones through the WORLD vocoder (`pyworld`), which costs a second or two per
sentence on the Pi on top of Piper.

## Spoken replies (Gemini)

With `GEMINI_API_KEY` in the **Pi's** `.env` (and internet on the Pi), what Roxy *says* to the
laptop mic comes from Gemini (`pi/dialog.py`). What she *does* is still decided by keywords
(`pi/commands.py`), straight away:

    "Roxy, could you sit down please?"  -> sits at once; Gemini writes the confirmation
    "Roxy, how are you?"                -> no action; Gemini answers
    "stop"                              -> stops, with the fixed "Stopping.", never waits for Gemini

Buttons and gestures keep their fixed lines. Sentences heard while Roxy is talking are ignored
(except stop), so the mic never answers her own voice. No key, no internet or a Gemini error:
she falls back to the fixed lines. `GEMINI_MODEL`, `GEMINI_MAX_WORDS`, `GEMINI_TIMEOUT` tune it.

## Stages

1. **Button -> speech.** Laptop button -> Pi -> Piper -> the Pi's speaker. No robot needed. *Done.*
2. **The robot link.** `LocalAP` + the AES key, then actions from the page's buttons. *Code written, untested — the robot has been off.*
3. **Camera.** Orbbec colour frames on the Pi, MJPEG to the browser so you can see what it sees.
4. **Hand pose.** Lifted from the parent repo's `go2_gesture/scripts/gesture_teleop.py`, which already does this — see `PLAN.md`. Fires the same intents as the voice path.

## Open question: whose speaker?

This plays through a speaker on the Pi, which is what the parent project does (USB sound card +
3.5 mm speaker). The Go2 Pro and EDU have a 3W speaker in the head; **whether the Air has one,
and whether it is reachable from custom code, is unverified** — several developers report the
Go2's speaker is not open to the SDK and used external speakers instead. `WebRTCAudioHub` in
`unitree_webrtc_connect` exposes `upload_audio_file` / `play_by_uuid`, so if it does work, a
pre-rendered Piper clip could come out of the robot itself. Thirty seconds to find out once the
robot is on:

```python
print(await WebRTCAudioHub(conn).get_audio_list())
```

Nothing here depends on the answer.
# roxy-ignite-demo
