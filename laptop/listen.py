"""The mic half, on the laptop: Moonshine speech-to-text -> a wake word filter -> the Pi.

The Bluetooth mic pairs to the laptop, not the Pi, so the mic lives here. That turns out to be
the right split anyway: Moonshine is CPU-bound and the laptop has more CPU to spare than a Pi
that is also decoding camera frames, and only the text crosses the Wi-Fi (~50 bytes a sentence
against ~32 KB/s for 16 kHz audio), which matters on the robot's own AP.

Filtering happens here so a room full of chatter doesn't reach the Pi at all; deciding what a
sentence *means* happens on the Pi (pi/commands.py), next to the robot. The name variants are
lifted from the main repo's wake_router.py -- Moonshine mishears "Roxy" constantly, so exact
names wake her alone while sound-alikes ("rocky", "foxy", "proxy") need a "hey" in front.

Everything is offline, but the model downloads on first use: run this once with internet before
you move to the robot's AP.

    python3 laptop/listen.py                   # reads ../.env
    python3 laptop/listen.py --no-wake         # every sentence is sent (quiet room, demo)
    python3 -m sounddevice                     # list mics, to set MIC_DEVICE
"""

import argparse
import asyncio
import json
import os
import pathlib
import queue
import re
import sys

NAMES = ["rocky", "rockie", "rocki", "rockey", "roki", "rokey", "heyrocky"]  # renamed from Roxy 2026-11-21: misheard too often
LOOSE = ["ricky", "rocco", "rocket", "rock", "rocks", "lucky"]  # need a prefix in front
PREFIXES = ["hey", "hi", "hay", "hello", "ok", "okay", "oi"]
STOP_WORDS = {"stop", "halt", "freeze"}                    # never need the wake word

pre = "|".join(map(re.escape, PREFIXES))
WAKE = re.compile(rf"\b(?:(?:{pre})\W+)?(?:{'|'.join(map(re.escape, NAMES))})\b"
                  rf"|\b(?:{pre})\W+(?:{'|'.join(map(re.escape, LOOSE))})\b", re.I)


def wanted(text: str, require_wake: bool) -> bool:
    """Whether this sentence is for us: the wake word, or a stop word on its own."""
    if any(w in re.findall(r"[a-z]+", text.lower()) for w in STOP_WORDS):
        return True
    return bool(WAKE.search(text)) if require_wake else True


def device_from(spec: str):
    """MIC_DEVICE as sounddevice wants it: an index, a name substring, or None for the default."""
    spec = (spec or "").strip()
    if spec in ("", "default"):
        return None
    return int(spec) if spec.isdigit() else spec


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("PI_URL", "ws://192.168.12.50:8080/ws"))
    ap.add_argument("--device", default=os.environ.get("MIC_DEVICE", "default"))
    ap.add_argument("--arch", default=os.environ.get("MOONSHINE_ARCH", "medium-streaming"))
    ap.add_argument("--no-wake", action="store_true", help="send every sentence, no wake word")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't contact the Pi: print what was heard and the intent it would trigger")
    args = ap.parse_args()

    import sounddevice
    import websockets
    from moonshine_voice import string_to_model_arch
    from moonshine_voice.mic_transcriber import MicTranscriber

    device = device_from(args.device)
    if device is not None:
        try:
            sounddevice.query_devices(device, "input")
        except ValueError as e:
            print(f"no input device {device!r} ({e}); using the default mic", file=sys.stderr)
            device = None

    # Moonshine calls on_line from its own worker thread, so hand lines to the loop by queue.
    lines: queue.Queue[str] = queue.Queue()
    mic = (MicTranscriber()
           .device(device)
           .model_arch(string_to_model_arch(args.arch))
           .on_line(lambda line: lines.put(line.text.strip()))
           .on_error(lambda e: print(f"mic: {e}", file=sys.stderr)))
    print(f"loading Moonshine {args.arch} (the first run downloads it) ...")
    mic.load()
    try:
        mic.set_keyterms(NAMES)  # biases the decoder towards the robot's name
    except Exception as e:
        print(f"keyterms not applied: {e}", file=sys.stderr)
    mic.start()
    print(f"listening on {'the default mic' if device is None else device!r}; "
          f"{'dry run, nothing is sent' if args.dry_run else f'sending to {args.url}'}", flush=True)

    if args.dry_run:  # the Pi's own matcher, so this shows exactly what it would do
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pi"))
        import commands
        try:
            while True:
                text = await asyncio.to_thread(lines.get)
                if text:
                    keep = wanted(text, not args.no_wake)
                    intent = commands.match(text) if keep else None
                    print(f"{'->' if keep else '  '} {text!r}"
                          + (f"  => {intent or 'no command'}" if keep else "  (no wake word)"), flush=True)
        except KeyboardInterrupt:
            return 0
        finally:
            mic.close()

    try:
        while True:  # the AP drops, the Pi reboots: reconnect rather than die
            try:
                # A silent Wi-Fi drop is only noticed when a ping goes unanswered, and anything said
                # meanwhile waits for that (60 s here once delayed a command by most of a minute).
                # 15 s still covers a busy Pi; raise it if the camera + hand tracking come back on.
                async with websockets.connect(args.url, ping_interval=5, ping_timeout=15) as ws:
                    print("connected to the Pi")
                    while True:
                        text = await asyncio.to_thread(lines.get)
                        if not text:
                            continue
                        keep = wanted(text, not args.no_wake)
                        print(f"{'->' if keep else '  '} {text!r}")
                        if keep:
                            try:
                                await ws.send(json.dumps({"type": "transcript", "text": text}))
                            except websockets.ConnectionClosed:
                                lines.put(text)  # the Pi restarted under us: resend it after reconnecting
                                raise
                        else:  # not for us, but the page shows everything the mic heard
                            await ws.send(json.dumps({"type": "heard", "text": text}))
            except (OSError, websockets.WebSocketException) as e:
                print(f"no link to the Pi ({e}); retrying in 2 s", file=sys.stderr)
                await asyncio.sleep(2)
    except KeyboardInterrupt:
        return 0
    finally:
        mic.close()


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")
    except ImportError:
        pass
    sys.exit(asyncio.run(main()) or 0)
