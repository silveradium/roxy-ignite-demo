"""Everything on the Pi, on one asyncio loop: the robot link, Piper, and a socket for the laptop.

    laptop browser  --HTTP/WS--> |      | --WebRTC--> Go2   (robot AP, no router, no internet)
    laptop mic      --WS------>  |  Pi  | --aplay-->  speaker
                                 |      | <--USB----  Orbbec        (stage 3)

The laptop never sends audio, only text: a sentence is ~50 bytes where 16 kHz mono is ~32 KB/s,
which matters on the robot's own AP. Both the button page and laptop/listen.py speak the same
WebSocket protocol, so a command has one code path whatever triggered it.

    in   {"type": "say",        "text": ...}   speak this, no robot action
         {"type": "transcript", "text": ...}   a heard sentence: match it, act, speak the reply
         {"type": "intent",     "name": ...}   a known intent straight up (the buttons)
         {"type": "heard",      "text": ...}   heard without the wake word: shown, never acted on
    out  {"type": "log",   "text": ...}        one line for the page's log
         {"type": "heard", "text", "wake", "intent"}  what the mic heard, for the page
         {"type": "state", ...}                broadcast whenever something changes

Synthesis blocks (Piper, then sox), so it runs in a thread; playback has its own queue in
audio.py. Nothing here fails fatally when the robot is absent -- the speech half still demos.
"""

import asyncio
import logging
import os
import pathlib
import sys
import threading

from aiohttp import WSMsgType, web

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import commands  # noqa: E402
import speech  # noqa: E402
import voices  # noqa: E402
from audio import Speaker  # noqa: E402
from robot import Go2  # noqa: E402

log = logging.getLogger("roxy")
HERE = pathlib.Path(__file__).parent


def env(name, default=""):
    return os.environ.get(name, default).strip()


class Demo:
    def __init__(self):
        self.voice_name = env("VOICE", "robot")
        self.sentence = env("SENTENCE", "Ruh-roh! Hello everyone, I am Roxy.")
        self.voices = speech.Voices(env("PIPER_VOICES_DIR", "./piper-voices"))
        self.spec = voices.spec_of(self.voice_name)
        self.speaker = Speaker(env("ALSA_DEVICE", "default"), float(env("GAIN_DB", "0") or 0))
        self.robot = Go2(env("GO2_CONN", "LocalAP"), env("GO2_IP") or None, env("GO2_AES_KEY") or None,
                         turn_gain=float(env("TURN_GAIN", "1.0") or 1.0))
        self.clients: set[web.WebSocketResponse] = set()
        self.gesture_stop = threading.Event()
        self.tasks: set[asyncio.Task] = set()

    # --- the one place a command is carried out ---------------------------------------------
    async def say(self, text: str) -> None:
        """Synthesise off the loop, then hand the clip to the speaker's queue."""
        if not text.strip():
            return
        wav = await asyncio.to_thread(speech.render, self.voices, text, self.spec)
        if wav:
            self.speaker.play(wav)

    async def do_intent(self, intent: str) -> str:
        action, reply = commands.resolve(intent)
        if action is None and reply is None:
            return f"unknown intent {intent!r}"
        # talk while it moves: the robot only answers a trick once it has finished (~17 s for a dance)
        spoken = asyncio.create_task(self.say(reply)) if reply else None
        if action is not None:
            name, parameter = action
            ok = await self.robot.act(name, parameter)
            why = ("robot not connected" if not self.robot.ready
                   else f"refused by the robot, code {self.robot.last_code}: is it standing?")
            await self.log(f"{intent} -> {name}{'' if ok else f' ({why})'}")
        if spoken:
            await spoken
        return intent

    def spawn(self, coro) -> None:
        """Run a command alongside the others, so a "stop" never queues behind a dance."""
        task = asyncio.create_task(self._then_broadcast(coro))
        self.tasks.add(task)  # a bare create_task can be garbage-collected mid-flight
        task.add_done_callback(self.tasks.discard)

    async def _then_broadcast(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("command failed")
        await self.broadcast(self.state())

    async def on_gesture(self, label: str, intent: str) -> None:
        await self.log(f"gesture: {label}")
        await self.do_intent(intent)
        await self.broadcast(self.state())

    async def on_transcript(self, text: str) -> None:
        intent = commands.match(text)
        await self.broadcast({"type": "heard", "text": text, "wake": True, "intent": intent})
        await self.log(f"heard: {text!r} -> {intent or 'ignored'}")
        if intent:
            await self.do_intent(intent)

    # --- talking to the page ----------------------------------------------------------------
    async def log(self, text: str) -> None:
        log.info(text)
        await self.broadcast({"type": "log", "text": text})

    def state(self) -> dict:
        return {"type": "state", "robot": self.robot.ready, "voice": self.voice_name,
                "speaking": self.speaker.speaking, "sentence": self.sentence,
                "voices": sorted(voices.LIBRARY), "intents": sorted(commands.COMMANDS)}

    async def broadcast(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except ConnectionError:
                self.clients.discard(ws)


# --- HTTP / WebSocket ------------------------------------------------------------------------

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    demo: Demo = request.app["demo"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    demo.clients.add(ws)
    await ws.send_json(demo.state())
    try:
        async for msg in ws:
            if msg.type is not WSMsgType.TEXT:
                continue
            try:
                data = msg.json()
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "say":
                await demo.say(data.get("text") or demo.sentence)
            elif kind == "transcript":
                demo.spawn(demo.on_transcript(data.get("text", "")))
            elif kind == "heard":  # the mic heard it, but without the wake word: show it only
                await demo.broadcast({"type": "heard", "text": data.get("text", ""), "wake": False,
                                      "intent": None})
                continue  # nothing changed, so no state broadcast
            elif kind == "intent":
                demo.spawn(demo.do_intent(data.get("name", "")))
            elif kind == "voice":
                name = data.get("name", "")
                if name in voices.LIBRARY:
                    demo.voice_name, demo.spec = name, voices.spec_of(name)
                    await demo.log(f"voice: {name}")
            await demo.broadcast(demo.state())
    finally:
        demo.clients.discard(ws)
    return ws


async def turn_handler(request: web.Request) -> web.Response:
    """Calibrating TURN_GAIN by eye: POST {"deg": 90, "gain": 1.2, "label": "test one"} says the
    label, then turns. The gain that turns a clean 90 degrees goes in .env."""
    demo: Demo = request.app["demo"]
    body = await request.json()
    deg, gain = float(body.get("deg", 90)), float(body.get("gain", demo.robot.turn_gain))
    if body.get("label"):
        await demo.say(str(body["label"]))
    ok = await demo.robot.act("turn", {"deg": deg, "gain": gain})
    await demo.log(f"test turn {deg:g} deg, gain {gain:g}{'' if ok else ' (robot not connected)'}")
    return web.json_response({"deg": deg, "gain": gain, "sent": ok})


async def say_handler(request: web.Request) -> web.Response:
    """Plain HTTP, so `curl -X POST .../say -d 'hello'` works without a browser."""
    demo: Demo = request.app["demo"]
    text = (await request.text()) or demo.sentence
    await demo.say(text)
    return web.json_response({"said": text})


async def on_startup(app: web.Application) -> None:
    demo: Demo = app["demo"]
    installed = demo.voices.installed()
    log.info("piper voices installed: %d (%s ...)", len(installed), ", ".join(installed[:3]))
    if demo.voice_name not in voices.LIBRARY:
        log.warning("VOICE=%r is not a named voice; using robot", demo.voice_name)
        demo.voice_name, demo.spec = "robot", voices.spec_of("robot")
    # never blocks startup (the speech half must demo), and keeps trying until the robot answers
    demo.robot_task = asyncio.create_task(demo.robot.connect_forever())
    if env("GESTURES", "0") == "1":
        threading.Thread(target=gesture_thread, args=(demo, asyncio.get_running_loop()),
                         daemon=True).start()


def gesture_thread(demo: "Demo", loop: asyncio.AbstractEventLoop) -> None:
    """The camera loop, off the event loop: a held sign runs its intent as a button would.
    Nothing fatal: without a camera the rest still demos, and a camera that drops off USB (the
    Pi 5 cuts every port on over-current) is reopened once it comes back."""
    device = env("CAMERA_DEVICE", "0")
    try:
        import gestures
    except Exception as e:
        log.error("gestures off: %s", e)
        return
    log.info("gestures on, camera %s: signs %s, circles %s", device, gestures.GESTURE_INTENTS,
             gestures.MOTION_INTENTS)
    last_error = None
    while not demo.gesture_stop.is_set():
        try:
            gestures.watch(device, stop=demo.gesture_stop,
                           on_fire=lambda label, intent: asyncio.run_coroutine_threadsafe(
                               demo.on_gesture(label, intent), loop),
                           on_open=lambda: log.info("camera open") if last_error else None)
        except Exception as e:
            if str(e) != last_error:  # once per kind of failure, not every 3 s
                log.error("camera lost (%s); retrying every 3 s", e)
            last_error = str(e)
            demo.gesture_stop.wait(3)


async def on_cleanup(app: web.Application) -> None:
    app["demo"].gesture_stop.set()
    await app["demo"].robot.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE.parent / ".env")
    except ImportError:
        pass
    app = web.Application()
    app["demo"] = Demo()
    app.add_routes([web.get("/ws", ws_handler),
                    web.post("/say", say_handler),
                    web.post("/turn", turn_handler),
                    web.get("/", lambda r: web.FileResponse(HERE / "static" / "index.html")),
                    web.static("/static", HERE / "static")])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    web.run_app(app, host=env("PI_HOST", "0.0.0.0"), port=int(env("PI_PORT", "8080")))


if __name__ == "__main__":
    main()
