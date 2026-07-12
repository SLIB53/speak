"""speak — thin client for speakd. Stdlib-only: starts in ~30 ms."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile

from . import default_socket_path, preprocess


def die(msg: str, code: int = 1) -> int:
    print(f"speak: {msg}", file=sys.stderr)
    return code


def request(sock_path: str, header: dict, text: str, timeout: float):
    """Returns (status_dict, wav_byte_iterator)."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    conn.connect(sock_path)
    conn.sendall(json.dumps(header).encode() + b"\n" + text.encode("utf-8"))
    conn.shutdown(socket.SHUT_WR)          # signal EOF; keep read side open

    buf = b""
    while b"\n" not in buf:
        data = conn.recv(4096)
        if not data:
            raise ConnectionError("daemon closed connection before status")
        buf += data
    line, _, rest = buf.partition(b"\n")
    status = json.loads(line.decode("utf-8"))

    def wav_chunks():
        if rest:
            yield rest
        while True:
            data = conn.recv(65536)
            if not data:
                conn.close()
                return
            yield data

    return status, wav_chunks()


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="speak",
        description="Local TTS client: text in, WAV out (via speakd).")
    ap.add_argument("-i", "--input", help="input text file (default: stdin)")
    ap.add_argument("-o", "--output", help="output WAV file (default: stdout)")
    ap.add_argument("--play", action="store_true",
                    help="play audio on macOS via afplay instead of writing")
    ap.add_argument("--voice", default="af_heart",
                    help="Kokoro voice id (default: af_heart; see VOICES.md; "
                         "e.g. am_michael, bf_emma)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="speech rate multiplier (default 1.0)")
    ap.add_argument("--para-pause", type=float, default=0.6, metavar="SEC",
                    help="silence between paragraphs in seconds (default 0.6)")
    ap.add_argument("--raw", action="store_true",
                    help="skip text preprocessing (acronym spelling, etc.)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print preprocessed text and exit (no daemon needed)")
    ap.add_argument("--socket", default=default_socket_path(),
                    help="daemon socket path (or $SPEAK_SOCKET)")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="request timeout in seconds (default 600)")
    args = ap.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            text = f.read()
    else:
        if sys.stdin.isatty():
            print("reading from stdin; end with Ctrl-D", file=sys.stderr)
        text = sys.stdin.read()

    if not text.strip():
        return die("no input text")

    if not args.raw:
        text = preprocess(text)

    if args.dry_run:
        print(text)
        return 0

    if not args.play and not args.output and sys.stdout.isatty():
        return die(
            "stdout is a terminal; redirect it, or use -o/--play\n"
            "  e.g.  speak < a.txt > a.wav\n"
            "        speak < a.txt | ffplay -autoexit -nodisp -i -", 2)

    header = {"voice": args.voice, "speed": args.speed,
              "para_pause": args.para_pause}
    try:
        status, chunks = request(args.socket, header, text, args.timeout)
    except (OSError, ConnectionError) as e:
        return die(f"cannot reach daemon at {args.socket} ({e})\n"
                   "  start it with:  uv run speakd &")

    if not status.get("ok"):
        return die(f"daemon error: {status.get('error', 'unknown')}")

    if args.play:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            for c in chunks:
                tmp.write(c)
            path = tmp.name
        try:
            subprocess.run(["afplay", path], check=False)
        finally:
            os.unlink(path)
        return 0

    out = open(args.output, "wb") if args.output else sys.stdout.buffer
    try:
        for c in chunks:
            out.write(c)
    finally:
        if args.output:
            out.close()
            print(f"wrote {args.output} "
                  f"({status.get('seconds', '?')}s of audio)",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
