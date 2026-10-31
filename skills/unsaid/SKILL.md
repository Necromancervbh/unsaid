---
name: unsaid
description: Edit a talking-head video or podcast by editing its transcript with the unsaid CLI. Removes filler words, cuts sentences, tightens pauses, or cuts a long recording down to a highlight of a target length. Use when the user asks to trim, clean up, shorten, or cut a video or audio file by what is said in it.
---

# Edit video by editing its transcript (unsaid)

`unsaid` transcribes a recording locally and cuts the media wherever words were deleted from the transcript. It can only **remove** speech. It never reorders, duplicates or changes words, so plan every edit as a deletion.

Check that it's installed with `unsaid --version`. If it isn't, install it with `uv tool install git+https://github.com/Necromancervbh/unsaid` (or `pipx install` the same URL).

## Workflow

1. **Transcribe.**

   ```bash
   unsaid transcribe talk.mp4
   ```

   This writes two files next to the input:
   - `talk.unsaid.txt`: the script, one sentence per line.
   - `talk.unsaid.json`: every word with `start`/`end` in seconds.

   The first run downloads the Whisper model (`small`, about 480 MB). Add `--model base` for a faster, rougher pass, and `--language en` to skip language detection. Re-running is instant because the transcript is cached.

2. **Decide the cut.** Read `talk.unsaid.txt`. When the user gives a target length, add up sentence durations from `talk.unsaid.json`: a sentence runs from its first word's `start` to its last word's `end`.

3. **Write an edited copy** of the script, for example `talk.edit.txt`. The rules for the copy:
   - Only delete whole words or whole lines. Keep everything else byte-for-byte.
   - Don't fix typos, punctuation or capitalization. Changed words are kept, not cut, and unsaid reports them as "differ from the transcript".
   - Don't move or repeat text: order can't change.

4. **Dry run, check, adjust.**

   ```bash
   unsaid cut talk.mp4 --script talk.edit.txt --dry-run
   ```

   stdout shows the transcript with every cut word in `[brackets]`. stderr ends with a summary like `removed 14 words (4 fillers), 4 cuts: 0:30.7 -> 0:21.3 (-9.4s)`. Compare the duration after `->` with the target, and edit `talk.edit.txt` again until it's right.

5. **Render.**

   ```bash
   unsaid cut talk.mp4 --script talk.edit.txt
   ```

   The output is `talk.unsaid.mp4`, or `.m4a` for audio-only input. Use `-o` to choose another path; unsaid refuses to overwrite the input. The last line of stdout is the output path.

## Options worth knowing

| Option | Effect |
|---|---|
| (default) | Filler sounds (um, uh, hmm, ...) are cut as well as your deletions. |
| `--no-fillers` | Only cut what the script deletes. |
| `--aggressive` | Also cut "like", "you know", "I mean", ... when they're set off by commas. |
| `--filler WORD` | Add a verbal tic to cut. Repeat it for more, e.g. `--filler okay --filler "sort of"`. |
| `--max-pause 0.6` | Shorten every pause longer than 0.6 s. Good for tightening a talk. |
| `--captions srt` | Also write captions timed to the edit (`srt` or `vtt`), next to the output. |
| `--burn-captions` | Burn captions into the video, one short line at a time with the spoken word highlighted. For Shorts, Reels and TikTok. |
| `-v` | Print the marked-up transcript while rendering. |

## Tips

- Cuts always land in the silence between kept words. Deleting the end of one sentence and the start of the next joins them naturally.
- For a highlight, keep whole sentences that make sense on their own. Delete the rest a line at a time; that's easier to verify than scattered single words.
- Whisper can mishear words. If a word is wrong in the script, still delete or keep it as written; don't correct it.
- Tell the user the before → after duration from the summary line, and the output path.
