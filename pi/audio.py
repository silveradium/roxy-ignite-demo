"""Plays WAV bytes on the Pi's speaker through aplay, one clip at a time.

A worker thread owns the queue, so synthesis (slow) and playback (slower) never block the
asyncio server. `speaking` is true from the first clip until the queue drains; once a mic is
involved, gate it on that so Roxy never transcribes itself.

gain_db boosts past the sound card's own maximum, soft-limited so loud peaks round off instead
of clipping. boost() is lifted from the main repo's robot/pi/.../speaker_node.py.
"""

import io
import logging
import queue
import subprocess
import threading
import wave

import numpy as np

log = logging.getLogger(__name__)
KNEE = 0.5  # linear below this fraction of full scale, tanh above, never past 1


def boost(wav: bytes, gain_db: float) -> bytes:
    """The WAV file louder by gain_db, peaks soft-limited. Unchanged if gain_db is 0 or not 16-bit."""
    with wave.open(io.BytesIO(wav)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    if not gain_db or params.sampwidth != 2:
        return wav
    x = np.frombuffer(frames, np.int16).astype(np.float32) / 32768 * 10 ** (gain_db / 20)
    over = np.abs(x) > KNEE
    x[over] = np.sign(x[over]) * (KNEE + (1 - KNEE) * np.tanh((np.abs(x[over]) - KNEE) / (1 - KNEE)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class Speaker:
    def __init__(self, device: str, gain_db: float = 0.0):
        self.device, self.gain_db = device, gain_db
        self.clips: queue.Queue[bytes] = queue.Queue()
        self.speaking = False
        threading.Thread(target=self._loop, daemon=True).start()

    def play(self, wav: bytes) -> None:
        """Queue a clip. Returns at once; playback happens on the worker thread."""
        log.info("clip: %.0f KB", len(wav) / 1024)
        self.clips.put(wav)

    def _loop(self) -> None:
        while True:
            wav = self.clips.get()
            self.speaking = True
            try:  # plughw converts the clip's rate and channels to whatever the card takes
                subprocess.run(["aplay", "-q", "-D", self.device, "-"],
                               input=boost(wav, self.gain_db), check=True, timeout=120)
            except (subprocess.SubprocessError, OSError, wave.Error, EOFError) as e:
                log.error("playback failed: %s", e)
            if self.clips.empty():
                self.speaking = False
