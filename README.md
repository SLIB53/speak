# Speak

Local text-to-speech on Apple Silicon using [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) running on [MLX](https://github.com/ml-explore/mlx) via [mlx-audio](https://github.com/Blaizzy/mlx-audio). The app runs a resident daemon so the model loads once, and clients speak to it via a Unix socket.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and an Apple Silicon Mac.

```sh
uv sync
```

First daemon start downloads model weights from HuggingFace (`mlx-community/Kokoro-82M-bf16`, ~330 MB) into `~/.cache/huggingface`; fully offline afterward.


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
alias speak='uv run --project /path/to/speak speak'
alias speakd='uv run --project /path/to/speak speakd'
```

To run the daemon automatically at login, wrap `uv run --project /path/to/speak speakd` in a launchd agent (`~/Library/LaunchAgents`).


### Client Options

| Flag            | Default                     | Meaning                                                                                 |
| --------------- | --------------------------- | --------------------------------------------------------------------------------------- |
| `-i / --input`  | stdin                       | input text file                                                                         |
| `-o / --output` | stdout                      | output WAV path                                                                         |
| `--play`        | off                         | play via macOS `afplay`                                                                 |
| `--voice`       | `af_heart`                  | Kokoro voice id ([list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)) |
| `--speed`       | `1.0`                       | speech rate multiplier                                                                  |
| `--para-pause`  | `0.6`                       | seconds of silence between paragraphs                                                   |
| `--raw`         | off                         | skip text preprocessing                                                                 |
| `--dry-run`     | off                         | print preprocessed text, no synthesis                                                   |
| `--socket`      | `~/.cache/speak/speak.sock` | daemon socket (or `$SPEAK_SOCKET`)                                                      |


### Daemon Options

| Flag          | Default                         | Meaning                               |
| ------------- | ------------------------------- | ------------------------------------- |
| `--model`     | `mlx-community/Kokoro-82M-bf16` | try `-8bit` / `-4bit` for less memory |
| `--socket`    | `~/.cache/speak/speak.sock`     | listen path                           |
| `--no-warmup` | off                             | skip warm-up generation at startup    |

The daemon handles requests sequentially (MLX generation isn't safely concurrent), and logs the per-request real-time factor to stderr.


## Use Cases

### macOS Integration

Because `speak --play` produces audio from standard input, it's very straightforward to integrate Speak throughout macOS using the Shortcuts app. Just use a 'Run Shell Script' action and set the script to `uv run --project /path/to/speak speak --play`, and set the 'Pass Input' field to `to stdin`. In the information panel, you can enable where its available throughout macOS (e.g., Share Sheet, Spotlight, etc.).

For example, your shortcut may look something like this:
```
                                 ⎸
‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
    Receive [Any] from [Share Sheet, Quick Actions, Search Result]

    If there's no input:
    [Ask For][Text]

‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
                                 ⎸
‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
  Run Shell Script

      uv run --project /Users/akil/speak speak --play

    Shell: [fish...]
    Input: [Shortcut Input]
    Pass Input: [to stdin]
    Run as Administrator: [ ]

‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾
```

And in the information panel in the right sidebar (second tab):

```
- [x] Show in Share Sheet
- [x] Show in Spotlight
  - [x] Receive Input from Spotlight
- [ ] Receive What's On Screen
- [x] Use as Quick Action
  - [x] Finder
  - [x] Services Menu
- [ ] Provide Output
```

This will effectively replace the built-in Text-to-Speech (often available via right-click > Speech) with Speak (Service > Speak), and make TTS available via Share, Spotlight, and anywhere else Shortcuts is used (e.g., Control Center, menu bar, etc.). You'll also get a running indicator which will allow you to cancel a running speech, which the built-in TTS doesn't seem to do.

#### Troubleshooting

macOS sandboxes these invocations, so you might receive an operation not permitted error from Shortcuts. You will have to grant your shell binary disk access (likely Full Disk Access) in System Settings > Privacy & Security.


## Notes

### Wire Protocol

Unix domain socket, one request per connection:

- client sends one JSON line `{"voice": ..., "speed": ..., "para_pause": ...}`,
  then raw UTF-8 text, then half-closes the write side;
- daemon replies with one JSON status line `{"ok": true, "seconds": ...}`
  (or `{"ok": false, "error": ...}`), then streams WAV bytes and closes.


### Text Preprocessing

Acronym fumbles and missing pauses are text problems, not model problems, so they're fixed client-side before synthesis: all-caps tokens are spelled out (`API` → `A P I`) unless whitelisted as pronounce-as-word (NASA, JSON, … — extend `PRONOUNCE_AS_WORD` in `src/speak/__init__.py` or add lines to `~/.config/speak/pronounce_as_word.txt`); literal replacements (`SQL` → "sequel", `e.g.` → "for example") live in `REPLACEMENTS`; em-dashes become comma-pauses; markdown syntax and link URLs are stripped; paragraph breaks get explicit silence. Run `--dry-run` to see exactly what will be spoken.


### Note on Dependencies

`pyproject.toml` deliberately avoids `misaki[en]`: that extra declares `spacy-curated-transformers`, which drags in PyTorch that misaki never imports (verified against misaki 0.9.4 — it only uses the CPU `en_core_web_sm` spacy pipeline). The actual runtime deps are declared directly instead, and misaki is pinned `<0.10` since that audit is version-specific.

