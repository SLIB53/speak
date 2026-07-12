"""
speak — local TTS with a resident daemon (Kokoro-82M on MLX).

Two entry points:

    speakd   daemon: loads Kokoro once via mlx-audio, serves a Unix socket
    speak    client: stdlib-only, ~30 ms startup; pipes text in, WAV out

    uv run speakd &                     # start once, model stays hot
    uv run speak < article.txt > article.wav
    pbpaste | uv run speak --play

Wire protocol (Unix domain socket):
    client -> daemon:  one JSON header line, then raw UTF-8 text until EOF
    daemon -> client:  one JSON status line {"ok": bool, ...}, then WAV bytes
"""

import os
import re

SAMPLE_RATE = 24000


def default_socket_path() -> str:
    return os.environ.get(
        "SPEAK_SOCKET",
        os.path.join(os.path.expanduser("~/.cache/speak"), "speak.sock"),
    )


# ---------------------------------------------------------------------------
# Text preprocessing — where "TTS fumbles acronyms / pauses" gets fixed
# ---------------------------------------------------------------------------

# All-caps tokens that ARE pronounced as words. Everything else in caps
# (2-6 letters) gets spelled out letter by letter. Extend here, or put one
# term per line in ~/.config/speak/pronounce_as_word.txt
PRONOUNCE_AS_WORD = {
    "NASA", "JSON", "YAML", "TOML", "REST", "SOAP", "RAM", "ROM", "LASER",
    "RADAR", "SCUBA", "POSIX", "UNIX", "LINUX", "GIF", "RAID",
}

# Explicit overrides (mixed pronunciation, symbols, abbreviations)
REPLACEMENTS = {
    "SQL": "sequel",          # change to "S Q L" if you're in that camp
    "kubectl": "kube control",
    "k8s": "kubernetes",
    "etc.": "etcetera",
    "e.g.": "for example",
    "i.e.": "that is",
    "vs.": "versus",
    "&": " and ",
}

_ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})(s?)\b")


def _load_user_words() -> set:
    path = os.path.expanduser("~/.config/speak/pronounce_as_word.txt")
    try:
        with open(path) as f:
            return {line.strip().upper() for line in f if line.strip()}
    except OSError:
        return set()


def _spell_acronym(m: re.Match, word_set: set) -> str:
    token, plural = m.group(1), m.group(2)
    if token in word_set:
        return m.group(0)
    letters = " ".join(token)          # "API" -> "A P I": read as letters
    if plural:
        letters += "'s"                # "APIs" -> "A P I 's"
    return letters


def preprocess(text: str) -> str:
    """Normalize text so Kokoro reads it the way a human would."""
    word_set = PRONOUNCE_AS_WORD | _load_user_words()

    for k in sorted(REPLACEMENTS, key=len, reverse=True):
        text = re.sub(re.escape(k), REPLACEMENTS[k], text)

    text = _ACRONYM_RE.sub(lambda m: _spell_acronym(m, word_set), text)

    # Em/en dashes read better as comma-pauses than as silence-eaten glyphs
    text = re.sub(r"\s*[—–]\s*", ", ", text)

    # Markdown debris that TTS engines mispronounce
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # [text](url) -> text

    text = text.replace("…", ".").replace("...", ".")

    # Collapse whitespace but preserve paragraph breaks (daemon pauses on those)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()
