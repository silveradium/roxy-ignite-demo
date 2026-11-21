"""Words -> what Roxy does. One table, so there is a single place to add a command.

The laptop decides *whether* a sentence was meant for us (wake word, see laptop/listen.py) and
sends the text; this decides *what it means*, next to the robot code. The gesture classifier
produces the same intent names (gestures.GESTURE_INTENTS), so every path lands here.

Matching is by keyword, anywhere in the sentence: "Roxy, can you do a dance for us?" is `dance`,
not just "Roxy dance". Keywords are whole words or phrases, with the forms people actually say
("dancing", "take a seat"). When a sentence holds several:
  - STOP always wins and needs no wake word.
  - Anything that isn't walking beats walking: "Roxy, come here and sit" sits rather than walks
    off. Walking is the one thing that keeps going until told otherwise, so it has to be the
    only thing asked for.
  - Otherwise the longest keyword wins, so "another dance" beats "dance".
"""

import re

STOP_WORDS = {"stop", "halt", "freeze", "stay"}

# intent -> (keywords that mean it, robot action, what Roxy says back)
COMMANDS = {
    "stop":  (STOP_WORDS,                      ("stop", None),              "Stopping."),
    "sit":   ({"sit", "sits", "sitting", "sit down", "lie down", "take a seat", "have a seat"},
              ("sit", None), "Ruh-roh, okay."),
    "stand": ({"stand", "standing", "stand up", "get up"}, ("stand_up", None), "Standing up."),
    "hello": ({"hello", "hi", "wave", "waving", "say hi"}, ("hello", None), "Hello there!"),
    "heart": ({"heart", "finger heart", "love you"}, ("heart", None),       "Love you too!"),
    "greet": ({"introduce yourself", "who are you", "your name", "about yourself"}, (None, None),
              "Ruh-roh! Hello everyone, I am Roxy."),
    # tricks: the dog needs to be standing, with a metre of room around it
    "dance": ({"dance", "dancing", "dance for us", "show us a dance"}, ("dance1", None),
              "Let's dance!"),
    "dance2": ({"another dance", "dance again", "second dance", "dance two", "one more dance",
                "different dance"}, ("dance2", None), "One more!"),
    "stretch": ({"stretch", "stretching", "stretch out", "big stretch"}, ("stretch", None),
                "Time for a big stretch."),
    # walking: timed motions (robot.py), cut short by "stop". No bare "left", "right", "back" or
    # "come": they turn up in ordinary talk ("you're so right", "come on"), and a misfire here
    # walks the dog off. Say what you mean: "turn left", "go back", "come here".
    "forward": ({"forward", "come here", "come forward", "walk", "go forward"},
                ("walk", {"m": 1.0}), "On my way."),
    "back":  ({"back up", "go back", "backward", "backwards", "reverse", "step back"},
              ("walk", {"m": -0.5}), "Backing up."),
    "left":  ({"turn left", "go left", "to the left", "left turn"},
              ("turn", {"deg": 90}), "Turning left."),
    "right": ({"turn right", "go right", "to the right", "right turn"},
              ("turn", {"deg": -90}), "Turning right."),
    "around": ({"turn around", "spin around", "full circle", "go around", "going around"},
               ("turn", {"deg": 360}), "Spinning around!"),
    # also drawn in the air with a pointing finger (gestures.MOTION_INTENTS). "counter clockwise"
    # contains "clockwise", but the longer keyword wins.
    "spin_cw": ({"clockwise", "turn clockwise", "spin clockwise"}, ("turn", {"deg": -360}),
                "Spinning clockwise!"),
    "spin_ccw": ({"anticlockwise", "anti clockwise", "counterclockwise", "counter clockwise"},
                 ("turn", {"deg": 360}), "Spinning anticlockwise!"),
}

WALKING = {"forward", "back", "left", "right", "around", "spin_cw", "spin_ccw"}


def match(transcript: str) -> str | None:
    """The intent a sentence means, or None. See the module docstring for who wins."""
    text = " " + re.sub(r"[^a-z0-9' ]+", " ", transcript.lower()).replace("'", "") + " "
    text = re.sub(r" +", " ", text)
    if any(f" {w} " in text for w in STOP_WORDS):
        return "stop"
    found = {}  # intent -> its longest keyword in the sentence
    for intent, (keywords, _, _) in COMMANDS.items():
        for k in keywords:
            if f" {k} " in text:
                found[intent] = max(found.get(intent, 0), len(k))
    if not found:
        return None
    if found.keys() - WALKING:  # anything else asked for: don't walk
        found = {i: n for i, n in found.items() if i not in WALKING}
    return max(found, key=found.get)


def resolve(intent: str) -> tuple[tuple[str, dict | None] | None, str | None]:
    """((action name, parameter) or None, what to say) for an intent; (None, None) if unknown."""
    if intent not in COMMANDS:
        return None, None
    _, action, reply = COMMANDS[intent]
    return (action if action[0] else None), reply
