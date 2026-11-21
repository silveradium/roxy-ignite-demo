"""What Roxy says to a heard sentence, from Gemini. The words she says only: what she *does* is
still decided by commands.match() on keywords, so a slow or missing Gemini never changes or
delays an action.

    reply(heard, action)  ->  text to speak
        heard   what the mic heard, wake word and all ("Roxy, could you sit down please?")
        action  what Roxy is doing about it: (intent, fixed reply) from commands.py, or None
                when no keyword matched and it is just conversation
    Fails soft: no key, no internet or a slow answer gives the action's fixed reply (or None for
    plain conversation, and Roxy stays quiet as she did before Gemini).

Adapted from the FYP's ros_ws/src/voice/voice/dialog_node.py (same google-genai client, persona
prompt, short history and minimal thinking), without its JSON intent schema: here the keywords
already chose the action. Settings in .env: GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MAX_WORDS,
GEMINI_TIMEOUT.
"""

import logging
import time

log = logging.getLogger(__name__)

PERSONA = """\
You are Roxy, a young, playful and charismatic robot dog (a Unitree Go2) at a live demo.
People talk to you through a microphone; each message is a speech-to-text transcript, so it can
contain recognition mistakes, and your name in it may come out as "Roxas", "Proxy" or "rocks".
Each message tells you what was heard and what your body is doing about it:
- If you are doing an action, confirm it in one short, lively sentence (at most ten words).
  Another system moves your body; never say you can't do it.
- If there is no action, answer like a friendly, witty young dog: at most two short sentences
  and about {max_words} words. You can sit, stand, wave, make a heart, dance, stretch, turn,
  spin and walk a little when asked by name. You cannot jump, flip, fetch or climb stairs:
  say so playfully.
Your answer is spoken aloud by text-to-speech: plain sentences only, no emoji, markdown, lists,
asterisks, stage directions or URLs; write numbers and symbols the way they are spoken. Do not
start with your own name. Be kind and correct first, funny second; if you don't know, say so."""


class Dialog:
    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", max_words: int = 30,
                 timeout: float = 8.0, history_turns: int = 4):
        self.model, self.max_words, self.history_turns = model, max_words, history_turns
        self.history: list[tuple[str, str]] = []  # (what they said + action, what Roxy said)
        self.client = None
        self.thinking = "minimal"
        if not api_key:
            log.warning("no GEMINI_API_KEY in .env: Roxy uses her fixed replies")
            return
        try:
            from google import genai
            from google.genai import types
            self.types = types
            self.client = genai.Client(api_key=api_key,
                                       http_options=types.HttpOptions(timeout=int(timeout * 1000)))
            log.info("Gemini replies on (%s)", model)
        except Exception as e:  # not installed, or a bad key format
            log.error("Gemini off (%s): Roxy uses her fixed replies", e)

    @property
    def on(self) -> bool:
        return self.client is not None

    def reply(self, heard: str, action: tuple[str, str] | None) -> str | None:
        """Blocking (run it in a thread). Never raises."""
        fallback = action[1] if action else None
        if self.client is None:
            return fallback
        prompt = f'Heard: "{heard}"\n' + (
            f"Action: you are doing \"{action[0]}\" now (the usual line would be: \"{action[1]}\")."
            if action else "Action: none, this is conversation.")
        t0 = time.monotonic()
        try:
            text = self._ask(prompt)
        except Exception as e:
            log.error("Gemini failed (%s): fixed reply instead", e)
            return fallback
        text = " ".join(text.replace("*", "").split())
        if not text:
            return fallback
        log.info("gemini %.1f s: %r", time.monotonic() - t0, text)
        self.history = (self.history + [(prompt, text)])[-self.history_turns:]
        return text

    def _ask(self, prompt: str) -> str:
        types = self.types
        contents = []
        for said, answered in self.history:
            contents.append(types.Content(role="user", parts=[types.Part(text=said)]))
            contents.append(types.Content(role="model", parts=[types.Part(text=answered)]))
        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
        config = types.GenerateContentConfig(
            system_instruction=PERSONA.format(max_words=self.max_words),
            temperature=0.8, max_output_tokens=256)
        if self.thinking:
            try:
                config.thinking_config = types.ThinkingConfig(thinking_level=self.thinking)
            except Exception:  # older google-genai without thinking_level
                self.thinking = ""
        try:
            response = self.client.models.generate_content(model=self.model, contents=contents,
                                                           config=config)
        except Exception as e:
            if not self.thinking or "thinking" not in str(e).lower():
                raise
            log.warning("%s rejected thinking_level, retrying without it", self.model)
            self.thinking = ""
            config.thinking_config = None
            response = self.client.models.generate_content(model=self.model, contents=contents,
                                                           config=config)
        return response.text or ""
