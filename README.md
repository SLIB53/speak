# speak (daemon + client)

Local text-to-speech on Apple Silicon: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
running on [MLX](https://github.com/ml-explore/mlx) via
[mlx-audio](https://github.com/Blaizzy/mlx-audio), behind a resident daemon so
the model loads once. The client is pure-stdlib and starts in ~30 ms.

No Homebrew, no torch: espeak-ng ships inside the `espeakng-loader` wheel,
and the MLX backend needs no PyTorch at all.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and an Apple Silicon Mac.

```sh
uv sync
```

First daemon start downloads model weights from HuggingFace
(`mlx-community/Kokoro-82M-bf16`, ~330 MB) into `~/.cache/huggingface`;
fully offline afterward.

## Usage

```sh
uv run speakd &                          # start the daemon; model stays hot

uv run speak < article.txt > article.wav
cat article.txt | uv run speak | ffplay -autoexit -nodisp -i -
pbpaste | uv run speak --play            # clipboard -> speakers
uv run speak -i article.txt -o out.wav --voice am_michael --speed 0.95
uv run speak --dry-run < article.txt     # audit text normalization (no daemon)
```

Handy aliases:

```sh
alias speak='uv run --project /path/to/speak-tts speak'
alias speakd='uv run --project /path/to/speak-tts speakd'
```

To run the daemon automatically at login, wrap `uv run speakd` in a launchd
agent (`~/Library/LaunchAgents`), or just keep it in a tmux pane.

## Client options

| Flag | Default | Meaning |
|---|---|---|
| `-i / --input` | stdin | input text file |
| `-o / --output` | stdout | output WAV path |
| `--play` | off | play via macOS `afplay` |
| `--voice` | `af_heart` | Kokoro voice id ([list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)) |
| `--speed` | `1.0` | speech rate multiplier |
| `--para-pause` | `0.6` | seconds of silence between paragraphs |
| `--raw` | off | skip text preprocessing |
| `--dry-run` | off | print preprocessed text, no synthesis |
| `--socket` | `~/.cache/speak/speak.sock` | daemon socket (or `$SPEAK_SOCKET`) |

## Daemon options

| Flag | Default | Meaning |
|---|---|---|
| `--model` | `mlx-community/Kokoro-82M-bf16` | try `-8bit` / `-4bit` for less memory |
| `--socket` | `~/.cache/speak/speak.sock` | listen path |
| `--no-warmup` | off | skip warm-up generation at startup |

The daemon handles requests sequentially (MLX generation isn't safely
concurrent) and logs per-request real-time factor to stderr.

## Wire protocol

Unix domain socket, one request per connection:

- client sends one JSON line `{"voice": ..., "speed": ..., "para_pause": ...}`,
  then raw UTF-8 text, then half-closes the write side;
- daemon replies with one JSON status line `{"ok": true, "seconds": ...}`
  (or `{"ok": false, "error": ...}`), then streams WAV bytes and closes.

Trivially scriptable from anything that can speak to a Unix socket.

## Text preprocessing

Acronym fumbles and missing pauses are text problems, not model problems,
so they're fixed client-side before synthesis: all-caps tokens are spelled
out (`API` → `A P I`) unless whitelisted as pronounce-as-word (NASA, JSON, …
— extend `PRONOUNCE_AS_WORD` in `src/speak/__init__.py` or add lines to
`~/.config/speak/pronounce_as_word.txt`); literal replacements (`SQL` →
"sequel", `e.g.` → "for example") live in `REPLACEMENTS`; em-dashes become
comma-pauses; markdown syntax and link URLs are stripped; paragraph breaks
get explicit silence. Run `--dry-run` to see exactly what will be spoken.

## Note on dependencies

`pyproject.toml` deliberately avoids `misaki[en]`: that extra declares
`spacy-curated-transformers`, which drags in PyTorch that misaki never
imports (verified against misaki 0.9.4 — it only uses the CPU
`en_core_web_sm` spacy pipeline). The actual runtime deps are declared
directly instead, and misaki is pinned `<0.10` since that audit is
version-specific.
