"""The Go2 over WebRTC: connect in the robot's own AP mode, then send sport-mode commands.

AP mode is the point of this demo: the robot broadcasts its own Wi-Fi, the Pi joins it, and
nothing needs a router or the internet. `LocalSTA` with GO2_IP is there for development on a
shared LAN, and `Remote` is deliberately absent because it goes through Unitree's cloud.

Firmware >= 1.1.15 needs the per-device AES-128 key (GO2_AES_KEY). Commands follow
unitree_webrtc_connect/examples/go2/data_channel/sportmode/: the robot has to be in "normal"
motion mode before sport commands do anything, so connect() switches it if it isn't.
"""

import asyncio
import json
import logging
import math
import time

log = logging.getLogger(__name__)

STREAM_HZ = 10    # Move requests per second while a timed motion runs
TURN_RATE = 0.8   # rad/s of yaw while turning (~46 deg/s)
WALK_SPEED = 0.4  # m/s while walking

# Sport-mode api_ids worth having on a button (unitree_webrtc_connect/constants.py, SPORT_CMD)
ACTIONS = {
    "damp": 1001, "balance_stand": 1002, "stop": 1003, "stand_up": 1004,
    "move": 1008, "sit": 1009, "rise_sit": 1010, "hello": 1016,
    "heart": 1036,  # FingerHeart in normal mode, Heart in mcf: the same id in both
    "stretch": 1017, "dance1": 1022, "dance2": 1023,  # same ids in normal and mcf
}


class Go2:
    """Nothing is sent until connect() succeeds; `ready` says whether it did.

    Everything here is awaited on the server's single event loop, so there is no locking.
    """

    def __init__(self, method: str = "LocalAP", ip: str | None = None, aes_key: str | None = None,
                 turn_gain: float = 1.0):
        self.method, self.ip, self.aes_key = method, ip, aes_key
        # turn time = gain x angle / TURN_RATE; above 1 because the robot ramps up and slips.
        # Calibrated by eye on the real robot (TURN_GAIN in .env).
        self.turn_gain = turn_gain
        self.conn = None
        self.ready = False
        self.motion: asyncio.Task | None = None
        self.last_code = None  # the robot's status code for the last request (0 = done)
        self.last_error = None  # the last connect failure, so a retry loop logs it once

    async def connect(self) -> bool:
        from unitree_webrtc_connect.constants import RTC_TOPIC
        from unitree_webrtc_connect.webrtc_driver import (UnitreeWebRTCConnection,
                                                          WebRTCConnectionMethod)
        method = getattr(WebRTCConnectionMethod, self.method)
        kwargs = {"aes_128_key": self.aes_key} if self.aes_key else {}
        if self.method == "LocalSTA":
            kwargs["ip"] = self.ip
        if self.ip and not await self._signaling_open(self.ip):
            # the library probes these ports with blocking sockets (~3 s on the event loop per
            # try when the robot is off), so only hand over once one answers
            if self.last_error != "unreachable":
                log.error("robot not reachable at %s (off, or not on this Wi-Fi); retrying", self.ip)
            self.last_error, self.ready = "unreachable", False
            return False
        try:
            self.conn = UnitreeWebRTCConnection(method, **kwargs)
            await self.conn.connect()
            await self._normal_mode(RTC_TOPIC)
            self.ready, self.last_error = True, None
            log.info("robot connected (%s)", self.method)
        except Exception as e:  # a demo without the robot still speaks, so never fatal
            if str(e) != self.last_error:  # once per kind of failure, not every retry
                log.error("robot not connected (%s): %s", self.method, e)
            self.last_error, self.ready = str(e), False
            await self.close()  # don't leave a half-open peer behind before the next try
        return self.ready

    @staticmethod
    async def _signaling_open(ip: str, timeout: float = 1.5) -> bool:
        """Whether the robot's WebRTC signaling port (9991 new firmware, 8081 old) accepts a
        connection, checked without blocking the event loop."""
        for port in (9991, 8081):
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
                writer.close()
                return True
            except (OSError, asyncio.TimeoutError):
                pass
        return False

    @property
    def alive(self) -> bool:
        """The data channel is still open. It closes when the robot drops off the Wi-Fi or is
        switched off, while `ready` would otherwise stay True from the first connect."""
        dc = getattr(self.conn, "datachannel", None)
        return bool(dc is not None and getattr(dc, "data_channel_opened", False))

    def _check_link(self) -> None:
        if self.ready and not self.alive:
            log.warning("robot link lost; reconnecting every few seconds")
            self.ready = False
            self._cancel_motion()

    async def connect_forever(self, every: float = 5.0) -> None:
        """Keep the robot connected for the life of the server: retry until it answers, then
        watch the link and reconnect when it drops. The robot takes one WebRTC client at a time
        and holds on to a client that vanished (a killed server) for a while, so the first tries
        can be refused."""
        while True:
            self._check_link()
            if not self.ready:
                await self.close()
                await self.connect()
            await asyncio.sleep(every)

    async def _normal_mode(self, RTC_TOPIC) -> None:
        """Sport commands are ignored in "ai" mode, so make sure we are in "normal".

        Firmware >= 1.1.7 runs "mcf" (Multi-Control Framework) instead and rejects a switch to
        normal (code 7004). Every api_id in ACTIONS is the same in both, so mcf is left alone.
        """
        r = await self.conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1001})
        if r["data"]["header"]["status"]["code"] != 0:
            return
        mode = json.loads(r["data"]["data"])["name"]
        log.info("motion mode: %s", mode)
        if mode not in ("normal", "mcf"):
            await self.conn.datachannel.pub_sub.publish_request_new(
                RTC_TOPIC["MOTION_SWITCHER"], {"api_id": 1002, "parameter": {"name": "normal"}})
            await asyncio.sleep(5)  # it stands up

    async def act(self, name: str, parameter: dict | None = None) -> bool:
        """One action by name. False if we aren't connected or the name is unknown.

        "turn" ({"deg": +left/-right}) and "walk" ({"m": +forward/-back}) are timed motions: they
        start in the background and return at once, so a "stop" can cut them short. Anything
        else is one sport-mode request, awaited until the robot answers (a trick answers when it
        has finished). "stop" also cancels a running motion.
        """
        from unitree_webrtc_connect.constants import RTC_TOPIC
        if name in ("turn", "walk", "stop"):
            self._cancel_motion()
        self._check_link()
        if not self.ready or name not in ACTIONS and name not in ("turn", "walk"):
            log.warning("ignored action %r (ready=%s)", name, self.ready)
            return False
        if name == "turn":
            deg = float((parameter or {}).get("deg", 90))
            gain = float((parameter or {}).get("gain", self.turn_gain))
            secs = gain * math.radians(abs(deg)) / TURN_RATE
            self.motion = asyncio.create_task(self._stream_move(0, 0, math.copysign(TURN_RATE, deg), secs))
            return True
        if name == "walk":
            m = float((parameter or {}).get("m", 1.0))
            secs = abs(m) / WALK_SPEED
            self.motion = asyncio.create_task(self._stream_move(math.copysign(WALK_SPEED, m), 0, 0, secs))
            return True
        body = {"api_id": ACTIONS[name]}
        if parameter:
            body["parameter"] = parameter
        reply = await self.conn.datachannel.pub_sub.publish_request_new(RTC_TOPIC["SPORT_MOD"], body)
        try:
            self.last_code = reply["data"]["header"]["status"]["code"]
        except (TypeError, KeyError):
            self.last_code = None
        if self.last_code not in (0, None):
            # it answered "no": usually not standing (tricks need it up), or the app has control
            log.warning("robot refused %s (code %s)", name, self.last_code)
            return False
        return True

    def _cancel_motion(self) -> None:
        if self.motion and not self.motion.done():
            self.motion.cancel()

    def _send_move(self, x: float, y: float, z: float) -> None:
        """One Move request, fire-and-forget: at 10 Hz, waiting for each reply would lag."""
        from unitree_webrtc_connect.constants import RTC_TOPIC
        self.conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["SPORT_MOD"], {
            "header": {"identity": {"id": int(time.time() * 1000) % 2147483648, "api_id": ACTIONS["move"]}},
            "parameter": json.dumps({"x": x, "y": y, "z": z})}, "req")

    async def _stream_move(self, x: float, y: float, z: float, secs: float) -> None:
        """Hold a velocity for secs: the Go2 stops on its own soon after the last Move, so a
        single one only nudges it. Always ends with a zero Move and StopMove, cancelled or not."""
        log.info("motion x=%.2f z=%.2f for %.1f s", x, z, secs)
        end = time.monotonic() + secs
        try:
            while time.monotonic() < end:
                self._send_move(x, y, z)
                await asyncio.sleep(1 / STREAM_HZ)
        finally:
            if self.ready and self.conn is not None:
                self._send_move(0, 0, 0)
                from unitree_webrtc_connect.constants import RTC_TOPIC
                self.conn.datachannel.pub_sub.publish_without_callback(RTC_TOPIC["SPORT_MOD"], {
                    "header": {"identity": {"id": int(time.time() * 1000) % 2147483648,
                                            "api_id": ACTIONS["stop"]}}, "parameter": ""}, "req")

    async def close(self) -> None:
        if self.conn is not None:
            try:  # bounded: a half-open peer can hang here, and this sits in the retry loop
                await asyncio.wait_for(self.conn.disconnect(), timeout=5)
            except Exception:
                pass
            self.conn = None
