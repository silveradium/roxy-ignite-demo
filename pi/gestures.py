"""STAGE 4: hand signs from camera frames -> an intent name from commands.py.

Ported from ../hand-gesture-recognition-mediapipe (app.py and model/keypoint_classifier/):
MediaPipe's HandLandmarker finds the 21 hand landmarks, they are made relative to the wrist and
scaled into [-1, 1], and a small TFLite MLP classifies those 42 numbers into one of the signs in
gesture_model/keypoint_classifier_label.csv. The model is the retrain of 2026-11-20: 20 classes,
of which 18 (heart) and 19 (hello) are new; the source repo's label file names only the first 18.

Drawn circles come from the second, upstream model (model/point_history_classifier/, never
retrained): while the hand makes the `one` sign (index finger up), the index fingertip's last 16
positions are classified as Stop / Clockwise / Counter Clockwise / Move, and the most common
answer over the last 16 frames wins (app.py, POINTER_LABEL). Clockwise is as the person sees it:
the frame is mirrored, so a circle drawn clockwise in front of you is clockwise here too.

The frame is mirrored before detection, exactly as app.py does, because that is how the training
data was recorded; it also makes left/right match the person's when they face the dog.

Debouncing is the FYP's go2_gesture/scripts/gesture_teleop.py, applied to intents rather than
labels (so two signs mapped to one intent vote together): an intent counts once it wins 70% of
the last 10 frames, fires once, and fires again only after the hand is lowered (no steady sign
for 1 s) and a 6 s cooldown has passed. Without that, a hand passing through a pose on its way
somewhere else triggers it.

Worth not rediscovering: the FYP's older copy of this classifier (17 Oct) called a thumbs up
"call" and open hands "dislike" at 0.99 on MediaPipe's sample photos, so its trick mode switched
to finger rules (_finger_sign there). Check the retrained model on those signs before trusting it.
And commands.py's move intents are one-shot: driving continuously from a held sign needs a
command loop plus a deadman for when the hand leaves frame (no_hand_timeout in gesture_teleop.py).

    python pi/gestures.py                 # print signs live from the camera, to test
    python pi/gestures.py --device 2       # another /dev/videoN
"""

import csv
import pathlib
import time
from collections import Counter, deque
from dataclasses import dataclass

import numpy as np

MODEL_DIR = pathlib.Path(__file__).parent / "gesture_model"

# sign label -> intent name in commands.COMMANDS. A sign that isn't here never fires.
GESTURE_INTENTS: dict[str, str] = {
    "heart": "heart",  # class 18: Roxy does the finger heart
    "hello": "hello",  # class 19: Roxy waves back
    # Not palm or stop: tried 2026-11-21 as extra waves, and any open hand in view set them off.
}

# fingertip-circle label -> intent. Only while the hand makes POINTER_LABEL.
MOTION_INTENTS: dict[str, str] = {
    "Clockwise": "spin_cw",
    "Counter Clockwise": "spin_ccw",
}
POINTER_LABEL = "one"
TRAIL = 16  # fingertip positions per classification, and answers voted over (app.py history_length)


def load_labels(path=MODEL_DIR / "keypoint_classifier_label.csv") -> list[str]:
    with open(path, encoding="utf-8-sig") as f:
        return [row[0] for row in csv.reader(f) if row]


def landmark_points(landmarks, width: int, height: int) -> list[list[int]]:
    """MediaPipe's normalised landmarks -> pixel [x, y] pairs (app.py calc_landmark_list)."""
    return [[min(int(p.x * width), width - 1), min(int(p.y * height), height - 1)] for p in landmarks]


def preprocess(points: list[list[int]]) -> list[float]:
    """Relative to the wrist, flattened, scaled by the largest value (app.py pre_process_landmark).
    This has to match the training data exactly, so don't "improve" it."""
    base_x, base_y = points[0]
    flat = [v for x, y in points for v in (x - base_x, y - base_y)]
    biggest = max(map(abs, flat)) or 1
    return [v / biggest for v in flat]


def preprocess_trail(trail, width: int, height: int) -> list[float]:
    """Fingertip positions relative to the oldest, as fractions of the frame, flattened
    (app.py pre_process_point_history). Must match the training data, so don't improve it."""
    base_x, base_y = trail[0]
    return [v for x, y in trail for v in ((x - base_x) / width, (y - base_y) / height)]


class SignClassifier:
    """A TFLite MLP: features -> (label, softmax score). The keypoint model by default (42
    floats); pass the point-history model and its labels for fingertip trails (32 floats)."""

    def __init__(self, model=MODEL_DIR / "keypoint_classifier.tflite", labels=None, threads=1):
        from ai_edge_litert.interpreter import Interpreter
        self.interp = Interpreter(model_path=str(model), num_threads=threads)
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]["index"]
        self.out = self.interp.get_output_details()[0]["index"]
        self.labels = labels or load_labels()
        n = self.interp.get_output_details()[0]["shape"][-1]
        if n != len(self.labels):
            raise ValueError(f"model has {n} classes but the label file names {len(self.labels)}")

    def __call__(self, features) -> tuple[str, float]:
        self.interp.set_tensor(self.inp, np.array([features], dtype=np.float32))
        self.interp.invoke()
        probs = np.squeeze(self.interp.get_tensor(self.out))
        i = int(np.argmax(probs))
        return self.labels[i], float(probs[i])


@dataclass
class Sign:
    label: str
    score: float
    points: list  # 21 [x, y] pixel pairs in the mirrored frame
    motion: str | None = None  # what the fingertip is drawing, while pointing: "Clockwise", ...


class HandSigns:
    """One BGR frame in, the sign of the most prominent hand (or None) out."""

    def __init__(self, min_detection=0.7, min_tracking=0.5, mirror=True, threads=1):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        self.mp, self.mirror = mp, mirror
        self.landmarker = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(MODEL_DIR / "hand_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO,  # tracks across frames: cheaper than IMAGE
            num_hands=1,
            min_hand_detection_confidence=min_detection,
            min_tracking_confidence=min_tracking))
        self.classify = SignClassifier(threads=threads)
        self.classify_trail = SignClassifier(
            MODEL_DIR / "point_history_classifier.tflite",
            load_labels(MODEL_DIR / "point_history_classifier_label.csv"), threads)
        self.trail: deque = deque(maxlen=TRAIL)    # index fingertip, [0, 0] when not pointing
        self.motions: deque = deque(maxlen=TRAIL)  # the trail classifier's recent answers
        self.last_ms = 0

    def __call__(self, frame_bgr) -> Sign | None:
        import cv2
        if self.mirror:
            frame_bgr = cv2.flip(frame_bgr, 1)
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        ms = max(time.monotonic_ns() // 1_000_000, self.last_ms + 1)  # must strictly increase
        self.last_ms = ms
        result = self.landmarker.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb), ms)
        if not result.hand_landmarks:
            self.trail.append([0, 0])
            return None
        h, w = rgb.shape[:2]
        points = landmark_points(result.hand_landmarks[0], w, h)
        label, score = self.classify(preprocess(points))
        self.trail.append(points[8] if label == POINTER_LABEL else [0, 0])
        motion = None
        if len(self.trail) == TRAIL:
            m, p = self.classify_trail(preprocess_trail(self.trail, w, h))
            self.motions.append(m if p >= 0.5 else "Stop")  # app.py's score_th
            motion = Counter(self.motions).most_common(1)[0][0]
        return Sign(label, score, points, motion)

    def close(self) -> None:
        self.landmarker.close()


def intent_of(sign: Sign | None) -> str | None:
    """What a frame asks for: a drawn circle beats the static sign."""
    if sign is None:
        return None
    if sign.label == POINTER_LABEL and sign.motion in MOTION_INTENTS:
        return MOTION_INTENTS[sign.motion]
    return GESTURE_INTENTS.get(sign.label)


class Trigger:
    """Per-frame intents (or labels) in, one to act on (once) out. See the module docstring."""

    def __init__(self, wanted=None, history=10, vote=0.7, cooldown=6.0, rearm=1.0):
        self.wanted = wanted  # labels that may fire; None = all of them
        self.history: deque = deque(maxlen=history)
        self.vote, self.cooldown, self.rearm = vote, cooldown, rearm
        self.latched, self.latched_seen, self.cooldown_until = None, 0.0, 0.0

    def steady(self) -> str | None:
        """The sign held right now, or None."""
        if len(self.history) < self.history.maxlen:
            return None
        top, votes = Counter(self.history).most_common(1)[0]
        return top if votes >= self.vote * self.history.maxlen else None

    def update(self, label: str | None, now: float | None = None) -> str | None:
        now = time.monotonic() if now is None else now
        self.history.append(label)
        held = self.steady()
        if held is None:
            if self.latched and now - self.latched_seen > self.rearm:  # hand lowered: re-arm
                self.latched = None
            return None
        if held == self.latched:
            self.latched_seen = now
            return None
        self.latched, self.latched_seen = held, now
        if (self.wanted is not None and held not in self.wanted) or now < self.cooldown_until:
            return None
        self.cooldown_until = now + self.cooldown
        return held


def find_orbbec_color() -> str:
    """The Gemini 335's colour stream: UVC interface 4 (…:1.4), node index 0. Don't use the
    /dev/v4l/by-id link: every stream shares one serial, and after a USB reset (the Pi 5 cuts all
    ports on over-current) its index0 landed on the depth stream instead (2026-11-21)."""
    for dev in sorted(pathlib.Path("/sys/class/video4linux").glob("video*")):
        try:
            name = (dev / "name").read_text()
            index = (dev / "index").read_text().strip()
            iface = pathlib.Path(dev / "device").resolve().name
        except OSError:
            continue
        if ("Orbbec" in name or "Gemini" in name) and index == "0" and iface.endswith(":1.4"):
            return f"/dev/{dev.name}"
    raise OSError("no Orbbec colour stream (unplugged, or still re-enumerating)")


def open_camera(device: str, width=640, height=480):
    """device: "orbbec" (found by find_orbbec_color), an index, or a /dev path."""
    import cv2
    if device == "orbbec":
        device = find_orbbec_color()
    cap = cv2.VideoCapture(int(device) if str(device).isdigit() else device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if not cap.isOpened():
        raise OSError(f"cannot open camera {device}")
    return cap


def watch(device: str, on_fire, on_sign=None, stop=None, on_fps=None, on_open=None) -> None:
    """Blocking camera loop (run it in a thread): on_fire(what was seen, intent) when a mapped
    sign is held or a circle drawn, on_sign(Sign or None) whenever what it sees changes.
    Returns when stop.is_set()."""
    cap = open_camera(device)
    if on_open:
        on_open()
    signs, trigger = HandSigns(), Trigger()
    shown, frames, t0 = None, 0, time.monotonic()
    try:
        while stop is None or not stop.is_set():
            ok, frame = cap.read()
            if not ok:
                raise OSError(f"camera {device} stopped giving frames")
            sign = signs(frame)
            frames += 1
            seen = (sign.label, sign.motion if sign.label == POINTER_LABEL else None) if sign else None
            if seen != shown and on_sign:
                on_sign(sign)
            shown = seen
            fired = trigger.update(intent_of(sign))
            if fired:
                what = sign.motion if sign and fired in MOTION_INTENTS.values() else (sign.label if sign else fired)
                on_fire(what, fired)
            if on_fps and time.monotonic() - t0 >= 10:
                on_fps(frames / (time.monotonic() - t0))
                frames, t0 = 0, time.monotonic()
    finally:
        cap.release()
        signs.close()


def main() -> None:
    """Live test over SSH: no window, no robot, just what it sees."""
    import argparse
    import os
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--device", default=os.environ.get("CAMERA_DEVICE", "0"),
                   help="camera index or /dev/videoN (default $CAMERA_DEVICE, else 0)")
    a = p.parse_args()
    print(f"camera {a.device}; signs: {GESTURE_INTENTS}; circles: {MOTION_INTENTS}; Ctrl+C to stop",
          flush=True)
    try:
        watch(a.device,
              on_fire=lambda label, intent: print(f"FIRE {label} -> {intent}", flush=True),
              on_sign=lambda s: print(f"  {s.label:16} {s.score:.2f}"
                                      + (f"  drawing: {s.motion}" if s.label == POINTER_LABEL and s.motion else "")
                                      if s else "  (no hand)", flush=True),
              on_fps=lambda fps: print(f"  [{fps:.1f} fps]", flush=True))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
