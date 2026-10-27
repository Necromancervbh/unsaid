# unsaid

[![test](https://github.com/Necromancervbh/unsaid/actions/workflows/test.yml/badge.svg)](https://github.com/Necromancervbh/unsaid/actions/workflows/test.yml)

**Edit video by editing text.** unsaid is an open-source, local-first Descript alternative.

unsaid transcribes your video on your own machine. You delete words from the transcript and they're gone from the video. Filler words go in one command. No account, no API key, no upload.

![Deleting a sentence from talk.unsaid.txt and running unsaid cut: the sentence and the filler words disappear from the video, which goes from 30.7s to 21.3s](https://raw.githubusercontent.com/Necromancervbh/unsaid/main/docs/demo.gif)

```bash
unsaid cut talk.mp4                  # remove "um", "uh", "hmm" -> talk.unsaid.mp4
unsaid cut talk.mp4 --max-pause 0.6  # ...and tighten long pauses
```

## Install

```bash
uv tool install unsaid-video     # or: pipx install unsaid-video
```

The package is `unsaid-video` on PyPI; the command it installs is `unsaid`. For the latest from GitHub: `uv tool install git+https://github.com/Necromancervbh/unsaid`.

That's all you need. ffmpeg comes bundled through `imageio-ffmpeg`, and if an `ffmpeg` is on your `PATH` it's used instead. The Whisper model downloads on first run. The default `small` model is about 480 MB, and after that unsaid works offline.

On a laptop CPU, `small` transcribes about 3-4x faster than real time: a 4-minute interview takes about a minute. Rendering the cut takes seconds. An NVIDIA GPU with CUDA is used automatically when available.

## Usage

### Remove filler words

```bash
unsaid cut talk.mp4 --dry-run        # preview: prints the transcript with [um] cuts marked
unsaid cut talk.mp4                  # render talk.unsaid.mp4
unsaid cut talk.mp4 --aggressive     # also ", like," / ", you know," / ", I mean," when set off by commas
unsaid cut talk.mp4 --filler okay --filler "sort of"   # your own verbal tics
```

### Edit the script

```bash
unsaid transcribe talk.mp4           # writes talk.unsaid.txt, one sentence per line
# open talk.unsaid.txt, delete the sentences/words you don't want, save
unsaid cut talk.mp4 --script talk.unsaid.txt
```

Deleted words are cut from the video. Fixing a typo or capitalization in the script doesn't cut anything: unsaid only cuts deletions, and it tells you how many changed words it left alone.

### Options

| Flag | What it does |
|---|---|
| `--model` | Whisper model: `tiny`, `base`, `small` (default), `medium`, `large-v3`, `turbo`, or a local CTranslate2 model path |
| `--language en` | Skip language auto-detection |
| `--device cpu\|cuda` | `auto` (the default) uses CUDA when it works and falls back to CPU |
| `--max-pause S` | Shorten every pause longer than `S` seconds |
| `--no-fillers` | Only apply `--script` / `--max-pause` |
| `-o out.mp4` | Output path (default `<input>.unsaid.mp4`, or `.m4a` for audio-only input) |
| `--crf`, `--preset` | x264 quality and speed |

Transcripts are cached next to the input as `<input>.unsaid.json`, so re-cutting with different options is instant. Run `unsaid transcribe --force` to redo one.

## How it works

1. **Transcribe.** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) runs with word timestamps. Whisper normally "cleans up" disfluencies, so unsaid primes every 30-second window with a prompt full of fillers, which makes it keep them verbatim. Use `--no-verbatim` to turn this off. A voice-activity detector then checks for speech Whisper skipped. Whisper occasionally does, and that speech would otherwise look like a pause.
2. **Plan cuts.** Cuts only ever land in the silence *between* kept words, and never inside speech Whisper skipped. When a word is removed, the pause left behind is the *shorter* of the two pauses around it, so the edit keeps the speaker's rhythm instead of jamming words together. Whisper's word timestamps can be off by a tenth of a second, so each cut edge then moves into the silence the audio actually shows next to the kept word, which keeps the first and last sounds of words intact.
3. **Render.** ffmpeg trims and concatenates the kept ranges in one pass. Cut points snap to the video's frame grid, so audio and video stay exactly in sync however many cuts there are, and each audio cut gets an 8 ms fade to avoid clicks.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
.venv/Scripts/python -m pytest      # Windows; .venv/bin/python on macOS/Linux
```

The tests render real video through ffmpeg but don't need a Whisper model.

The GIF at the top is drawn by `scripts/make_demo_gif.py` from a real `unsaid cut` run: its transcript, cut plan, waveform and output.

## Roadmap

- [ ] Browser editor: `unsaid edit talk.mp4` opens the transcript, and striking text cuts the video live
- [ ] Agent mode: an MCP server plus `SKILL.md`, so Claude Code, Codex or Cursor can make the edit ("cut this to a 60-second highlight")
- [ ] Captions: SRT/VTT export retimed to the edit, plus animated burned-in captions for Shorts and Reels
- [ ] FCPXML/EDL export for Resolve, Premiere and Final Cut
- [ ] Stutter and repeated-word removal ("I I think")
- [ ] Overdub with a local voice clone, and "studio sound" cleanup

## Tested on real speech

Two unscripted English interviews (CC BY, from Wikimedia Commons), with default settings on a laptop CPU:

| Clip | Transcribe | Fillers cut | Result | Render |
|---|---|---|---|---|
| 4:20 press Q&A, background noise | 85 s | 23 | 4:20.3 → 4:03.2 | 8 s |
| 3:55 one-on-one interview | 62 s | 15 | 3:54.9 → 3:41.8 | 10 s |

To check the cuts, the output files were transcribed again and compared with the words unsaid meant to keep. They matched at 98.6% and 99.2%. Every difference was Whisper hearing a name or a word differently, none of them within two seconds of a cut. Two fillers that Whisper missed the first time stayed in. Audio and video stayed in sync to the frame through every cut.

## Known limitations

- Filler detection is only as good as Whisper's transcript. It's tuned for English: other languages are transcribed, but Whisper may clean up their fillers.
- Whisper can mishear words, or make one up. unsaid already drops the short, low-confidence segment Whisper tends to tack onto the end of a recording ("Good.", "Bye."). Delete any other made-up words in the script.
- Output is always re-encoded (H.264 + AAC) at the source's frame rate.

## License

MIT
