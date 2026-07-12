"""speakd — resident TTS daemon. Loads Kokoro-82M on MLX once, then serves
synthesis requests over a Unix domain socket. See package docstring for the
wire protocol."""

import argparse
import io
import json
import os
import signal
import socket
import sys
import time

from . import SAMPLE_RATE, default_socket_path

HEADER_LIMIT = 64 * 1024          # max header line length
TEXT_LIMIT = 4 * 1024 * 1024      # max request text (4 MB is a long book)
IO_TIMEOUT = 30.0                 # per-socket-op timeout, seconds


def log(msg: str) -> None:
    print(f"speakd: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, model_id: str):
        import numpy as np                      # noqa: F401 (fail fast here)
        from mlx_audio.tts.utils import load_model
        t0 = time.monotonic()
        self.model = load_model(model_id)
        log(f"loaded {model_id} in {time.monotonic() - t0:.1f}s")

    def synth(self, text: str, voice: str, speed: float,
              para_pause: float) -> bytes:
        """Full pipeline: text -> WAV bytes."""
        import numpy as np
        import soundfile as sf

        lang_code = voice[0] if voice else "a"  # kokoro convention
        gap = np.zeros(int(SAMPLE_RATE * para_pause), dtype=np.float32)
        chunks = []

        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        for pi, para in enumerate(paragraphs):
            if pi > 0 and para_pause > 0:
                chunks.append(gap)
            for result in self.model.generate(
                text=para, voice=voice, speed=speed,
                lang_code=lang_code, split_pattern=r"\n+",
            ):
                chunks.append(np.array(result.audio, dtype=np.float32))

        if not chunks:
            raise ValueError("no audio produced (empty text?)")

        buf = io.BytesIO()
        sf.write(buf, np.concatenate(chunks), SAMPLE_RATE,
                 format="WAV", subtype="PCM_16")
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Socket plumbing
# ---------------------------------------------------------------------------

def read_until_eof(conn: socket.socket, already: bytes) -> bytes:
    parts = [already]
    total = len(already)
    while True:
        data = conn.recv(65536)
        if not data:
            return b"".join(parts)
        total += len(data)
        if total > TEXT_LIMIT:
            raise ValueError(f"request exceeds {TEXT_LIMIT} bytes")
        parts.append(data)


def read_header_line(conn: socket.socket) -> tuple[dict, bytes]:
    """Read up to first newline; return (parsed header, leftover bytes)."""
    buf = b""
    while b"\n" not in buf:
        if len(buf) > HEADER_LIMIT:
            raise ValueError("header too long")
        data = conn.recv(4096)
        if not data:
            raise ValueError("connection closed before header")
        buf += data
    line, _, rest = buf.partition(b"\n")
    return json.loads(line.decode("utf-8")), rest


def handle(conn: socket.socket, engine: Engine) -> None:
    conn.settimeout(IO_TIMEOUT)
    try:
        header, rest = read_header_line(conn)
        text = read_until_eof(conn, rest).decode("utf-8")

        if header.get("op") == "ping":
            conn.sendall(b'{"ok": true, "pong": true}\n')
            return

        t0 = time.monotonic()
        wav = engine.synth(
            text,
            voice=str(header.get("voice", "af_heart")),
            speed=float(header.get("speed", 1.0)),
            para_pause=float(header.get("para_pause", 0.6)),
        )
        elapsed = time.monotonic() - t0
        audio_secs = (len(wav) - 44) / (SAMPLE_RATE * 2)
        log(f"synthesized {audio_secs:.1f}s audio in {elapsed:.2f}s "
            f"({audio_secs / max(elapsed, 1e-9):.1f}x real time)")

        status = json.dumps({"ok": True, "seconds": round(audio_secs, 2)})
        conn.sendall(status.encode() + b"\n" + wav)
    except Exception as e:                              # noqa: BLE001
        log(f"request failed: {e}")
        try:
            conn.sendall(json.dumps(
                {"ok": False, "error": str(e)}).encode() + b"\n")
        except OSError:
            pass
    finally:
        conn.close()


def bind_socket(path: str) -> socket.socket:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        # Stale socket from a dead daemon? Probe before stealing it.
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(1.0)
            probe.connect(path)
            probe.close()
            log(f"another daemon is already listening on {path}")
            sys.exit(1)
        except OSError:
            os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(8)
    return srv


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="speakd", description="Resident Kokoro TTS daemon (MLX).")
    ap.add_argument("--socket", default=default_socket_path(),
                    help=f"unix socket path (default {default_socket_path()})")
    ap.add_argument("--model", default="mlx-community/Kokoro-82M-bf16",
                    help="HF repo id; try mlx-community/Kokoro-82M-8bit or "
                         "-4bit for lower memory (default bf16)")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip warm-up generation at startup")
    args = ap.parse_args()

    engine = Engine(args.model)
    if not args.no_warmup:
        t0 = time.monotonic()
        engine.synth("Warm up.", "af_heart", 1.0, 0.0)
        log(f"warmed up in {time.monotonic() - t0:.1f}s")

    srv = bind_socket(args.socket)
    log(f"listening on {args.socket}")

    def shutdown(signum, frame):
        log("shutting down")
        try:
            srv.close()
            os.unlink(args.socket)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        handle(conn, engine)   # sequential: MLX generation isn't concurrent
    return 0


if __name__ == "__main__":
    sys.exit(main())
