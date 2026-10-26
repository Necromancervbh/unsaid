"""Render the README's hero GIF (docs/demo.gif) from a real unsaid run.

    unsaid transcribe talk.mp4
    # delete a sentence from talk.unsaid.txt
    python scripts/make_demo_gif.py talk.mp4 talk.unsaid.txt docs/demo.gif

Nothing in the animation is made up: the transcript, the words the edit deletes,
the fillers unsaid finds, the cut ranges, the waveform and the summary line all
come from the same code `unsaid cut` runs. The one cosmetic liberty: when the
cleaned-up text closes over a removed filler, the comma Whisper put in front of
it goes too ("the, uh, transcript" reads "the transcript"). Needs pillow, which
unsaid itself doesn't depend on: `uv pip install pillow`.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from unsaid.cli import _fmt
from unsaid.cli import main as unsaid_main
from unsaid.cuts import plan_cuts
from unsaid.fillers import find_fillers
from unsaid.render import default_output, ffmpeg_exe
from unsaid.script import removed_by_script
from unsaid.transcript import load_cached

W, H, FPS = 960, 540, 25
M = 24  # outer margin

BG, PANEL, TERM, TRACK = (13, 17, 23), (22, 27, 34), (10, 13, 18), (31, 37, 45)
BORDER, TEXT, DIM, FAINT = (48, 54, 61), (230, 237, 243), (125, 133, 144), (72, 79, 88)
BLUE, SEL, RED, RED_BG, GREEN = (88, 166, 255), (31, 64, 110), (248, 81, 73), (72, 36, 40), (63, 185, 80)
WAVE, WAVE_LIT = (110, 118, 129), (196, 206, 217)


def _font(size: int, *names: str) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


MONO_NAMES = ("consola.ttf", "Menlo.ttc", "DejaVuSansMono.ttf")
UI_NAMES = ("seguisb.ttf", "HelveticaNeue.ttc", "DejaVuSans-Bold.ttf")
MONO, MONO_S = _font(20, *MONO_NAMES), _font(16, *MONO_NAMES)
UI, UI_S = _font(21, *UI_NAMES), _font(14, *UI_NAMES)
CW, CW_S = MONO.getlength("x"), MONO_S.getlength("x")

EDITOR = (M, 58, W - M, 304)
TEXT_X, TEXT_Y, LH = M + 62, 114, 31
TERMINAL = (M, 316, W - M, 404)
TERM_X, TERM_Y, TERM_LH = M + 16, 338, 22
TIMELINE = (M, 416, W - M, H - M)
WAVE_X0, WAVE_X1, WAVE_Y, WAVE_H = M + 22, W - M - 22, 481, 19
TYPING = 40  # characters per second


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def progress(t: float, span: tuple[float, float]) -> float:
    return clamp((t - span[0]) / (span[1] - span[0]))


def ease(t: float, span: tuple[float, float]) -> float:
    x = progress(t, span)
    return x * x * (3 - 2 * x)


def mix(a: tuple, b: tuple, k: float) -> tuple:
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


@dataclass
class Tok:
    index: int  # word index in the transcript
    col: int  # character column in its line


class Demo:
    def __init__(self, media: Path, script: Path):
        t = load_cached(media)
        if t is None:
            raise SystemExit(f"no transcript for {media}; run `unsaid transcribe {media}` first")
        self.words, self.duration = t.words, t.duration
        self.by_script, _ = removed_by_script(t, script.read_text(encoding="utf-8"))
        self.fillers = sorted(find_fillers(t) - self.by_script)
        self.removed = self.by_script | set(self.fillers)
        plan = plan_cuts(t, self.removed)
        self.saved = plan.saved
        self.cuts = _complement(plan.keep, plan.duration)
        self.media_name, self.script_name = media.name, script.name
        self.command = f"unsaid cut {media.name} --script {script.name}"
        self.summary = _cli_summary(media, script)
        self.output = default_output(media, has_video=True).name
        self.bars = _levels(media, n=int((WAVE_X1 - WAVE_X0) / 3))
        self.bar_cut = [next((k for k, (a, b) in enumerate(self.cuts) if a <= tc < b), None) for tc, _ in self.bars]

        self.lines: list[list[Tok]] = []
        i = 0
        for line in t.to_text().splitlines():
            toks, col = [], 0
            for text in line.split(" "):
                assert text == self.words[i].text
                toks.append(Tok(i, col))
                col += len(text) + 1
                i += 1
            self.lines.append(toks)
        # Kept words whose trailing comma only separated them from a removed filler.
        self.drop_comma: set[int] = set()
        for toks in self.lines:
            idx = [tok.index for tok in toks]
            for k in range(1, len(idx)):
                prev = idx[k - 1]
                if prev in self.removed or idx[k] not in self.removed:
                    continue
                j = k
                while j + 1 < len(idx) and idx[j + 1] in self.removed:
                    j += 1
                if j + 1 < len(idx) and self.words[prev].text.endswith(",") and self.words[idx[j]].text.endswith(","):
                    self.drop_comma.add(prev)
        # How many characters close up in front of each word once the removed words go.
        self.shift: dict[int, int] = {}
        for toks in self.lines:
            gone = 0
            for tok in toks:
                self.shift[tok.index] = gone
                if tok.index in self.removed:
                    gone += len(self.words[tok.index].text) + 1
                elif tok.index in self.drop_comma:
                    gone += 1
        self.runs = _runs(sorted(self.by_script))

        # Storyboard, in seconds.
        self.sweep = (0.6, 3.0)
        s1 = 3.4
        self.caret_at = s1 + 0.2
        self.select = (s1 + 0.4, s1 + 1.4)
        self.delete = s1 + 1.8
        self.collapse_span = (s1 + 2.2, s1 + 2.6)
        s2 = self.saved_at = s1 + 3.0
        self.type_start = s2 + 0.2
        self.type_end = self.type_start + len(self.command) / TYPING
        self.enter = self.type_end + 0.35
        self.summary_at = self.enter + 0.3
        self.output_at = self.enter + 0.9
        s3 = self.output_at + 0.5
        self.cut = (s3 + 0.15, s3 + 1.55)
        self.end = self.cut[1] + 3.0
        self.captions = [(0.0, "unsaid turns your video into a text file"),
                         (s1, "Delete the words you don't want"),
                         (s2, "Run unsaid cut. Filler words go too"),
                         (s3, "They're gone from the video")]
        self.marked_at = {i: self.delete for i in self.by_script}
        for k, i in enumerate(self.fillers):
            self.marked_at[i] = self.enter + 0.25 + 0.12 * k
        self.cut_marked_at = [
            min((self.marked_at[i] for i in self.removed if self.words[i].start < b and self.words[i].end > a),
                default=self.cut[0])
            for a, b in self.cuts]

    # -- state at time t ---------------------------------------------------

    def collapse(self, li: int, t: float) -> float:
        indices = [tok.index for tok in self.lines[li]]
        if all(i in self.by_script for i in indices):
            return ease(t, self.collapse_span)
        if all(i in self.removed for i in indices):
            return ease(t, self.cut)
        return 0.0

    def selected(self, t: float) -> list[int]:
        if not self.select[0] <= t < self.delete:
            return []
        u = progress(t, self.select)
        return [i for run in self.runs for i in run[:min(len(run), int(u * len(run)) + 1)]]

    def playhead(self, t: float) -> float | None:
        if self.sweep[0] <= t < self.sweep[1]:
            return self.duration * progress(t, self.sweep)
        return None

    def word_at(self, time: float | None) -> int | None:
        if time is None:
            return None
        return max((i for i, w in enumerate(self.words) if w.start <= time), default=None)

    def removed_before(self, time: float) -> float:
        return sum(min(time, b) - a for a, b in self.cuts if time > a)

    # -- drawing -------------------------------------------------------------

    def frame(self, t: float) -> Image.Image:
        im = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(im)
        self.draw_caption(d, t)
        self.draw_editor(d, t)
        self.draw_terminal(d, t)
        self.draw_timeline(d, t)
        return im

    def draw_caption(self, d: ImageDraw.ImageDraw, t: float) -> None:
        num, (start, text) = max((n, c) for n, c in enumerate(self.captions, 1) if c[0] <= t)
        k = 1.0 if start == 0 else ease(t, (start, start + 0.3))
        cx, cy = M + 13, 30
        d.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), fill=mix(BG, BLUE, k))
        d.text((cx, cy), str(num), fill=BG, font=UI_S, anchor="mm")
        d.text((M + 36, cy), text, fill=mix(BG, TEXT, k), font=UI, anchor="lm")

    def draw_editor(self, d: ImageDraw.ImageDraw, t: float) -> None:
        x0, y0, x1, _ = EDITOR
        d.rounded_rectangle(EDITOR, radius=10, fill=PANEL, outline=BORDER)
        for k, color in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
            cx = x0 + 20 + 18 * k
            d.ellipse((cx - 6, y0 + 10, cx + 6, y0 + 22), fill=color)
        d.text((x0 + 84, y0 + 16), self.script_name, fill=DIM, font=UI_S, anchor="lm")
        if self.delete <= t < self.saved_at:  # unsaved-changes dot
            nx = x0 + 84 + UI_S.getlength(self.script_name) + 10
            d.ellipse((nx - 4, y0 + 12, nx + 4, y0 + 20), fill=DIM)
        d.line((x0, y0 + 32, x1 - 1, y0 + 32), fill=BORDER)

        collapse = [self.collapse(li, t) for li in range(len(self.lines))]
        visible = sum(1 - c for c in collapse)
        for k in range(len(self.lines)):
            if (a := clamp(visible - k)) > 0:
                d.text((TEXT_X - 22, TEXT_Y + LH * k), str(k + 1), fill=mix(PANEL, FAINT, a), font=MONO_S,
                       anchor="rm")

        current = self.word_at(self.playhead(t))
        selected = self.selected(t)
        y = TEXT_Y
        for li, toks in enumerate(self.lines):
            if (fade := clamp(2 * collapse[li])) < 1:
                self.draw_line(d, t, toks, y, fade, selected, current)
            y += LH * (1 - collapse[li])

    def draw_line(self, d, t, toks, y, fade, selected, current) -> None:
        p = ease(t, self.cut)
        xs = {tok.index: TEXT_X + CW * (tok.col - p * self.shift[tok.index]) for tok in toks}
        end_of = {tok.index: xs[tok.index] + CW * len(self.words[tok.index].text) for tok in toks}

        chosen = [tok.index for tok in toks if tok.index in selected]
        caret = None
        if chosen:
            right = end_of[chosen[-1]]
            if chosen[-1] == toks[-1].index and progress(t, self.select) >= 1:
                right += CW  # the line break is selected too
            d.rectangle((xs[chosen[0]] - 1, y - LH // 2 + 3, right, y + LH // 2 - 3), fill=SEL)
            caret = right
        elif self.runs and self.caret_at <= t < self.delete and self.runs[0][0] in xs:
            caret = xs[self.runs[0][0]]

        # Marked words, grouped so a deleted phrase gets one highlight and one strike line.
        groups: list[list[int]] = []
        for tok in toks:
            if tok.index in self.removed and t >= self.marked_at[tok.index]:
                if groups and groups[-1][-1] == tok.index - 1:
                    groups[-1].append(tok.index)
                else:
                    groups.append([tok.index])
        gone = max(fade, clamp(2 * p))
        if gone < 1:
            for g in groups:
                d.rounded_rectangle((xs[g[0]] - 3, y - 13, end_of[g[-1]] + 3, y + 13), radius=4,
                                    fill=mix(RED_BG, PANEL, gone))
        marked = {i for g in groups for i in g}

        for tok in toks:
            word, x = self.words[tok.index].text, xs[tok.index]
            if tok.index in marked:
                if gone < 1:
                    d.text((x, y), word, fill=mix(RED, PANEL, gone), font=MONO, anchor="lm")
                continue
            color = mix(BLUE if tok.index == current else TEXT, PANEL, fade)
            if tok.index in self.drop_comma and p > 0:
                d.text((x, y), word[:-1], fill=color, font=MONO, anchor="lm")
                d.text((x + CW * (len(word) - 1), y), ",", fill=mix(color, PANEL, clamp(2 * p)), font=MONO,
                       anchor="lm")
            else:
                d.text((x, y), word, fill=color, font=MONO, anchor="lm")
        if gone < 1:
            for g in groups:
                d.line((xs[g[0]] - 1, y + 1, end_of[g[-1]] + 1, y + 1), fill=mix(RED, PANEL, gone), width=2)
        if caret is not None:
            d.line((caret, y - 12, caret, y + 12), fill=BLUE, width=2)

    def draw_terminal(self, d: ImageDraw.ImageDraw, t: float) -> None:
        d.rounded_rectangle(TERMINAL, radius=10, fill=TERM, outline=BORDER)
        x, y = TERM_X, TERM_Y
        d.text((x, y), "$", fill=GREEN, font=MONO_S, anchor="lm")
        n = int(clamp((t - self.type_start) * TYPING, 0, len(self.command)))
        if n:
            d.text((x + 2 * CW_S, y), self.command[:n], fill=TEXT, font=MONO_S, anchor="lm")
        if t < self.enter and (self.type_start <= t < self.type_end or int(t * 2.5) % 2 == 0):
            bx = x + CW_S * (2 + n)
            d.rectangle((bx, y - 9, bx + CW_S - 1, y + 9), fill=mix(TERM, TEXT, 0.8))
        if t >= self.summary_at:
            split = self.summary.rfind("(")
            d.text((x, y + TERM_LH), self.summary[:split], fill=DIM, font=MONO_S, anchor="lm")
            d.text((x + CW_S * split, y + TERM_LH), self.summary[split:], fill=GREEN, font=MONO_S, anchor="lm")
        if t >= self.output_at:
            d.text((x, y + 2 * TERM_LH), self.output, fill=TEXT, font=MONO_S, anchor="lm")

    def draw_timeline(self, d: ImageDraw.ImageDraw, t: float) -> None:
        x0, y0, x1, _ = TIMELINE
        d.rounded_rectangle(TIMELINE, radius=10, fill=PANEL, outline=BORDER)
        p = ease(t, self.cut)

        hy = y0 + 20
        if p < 0.5:
            d.text((x0 + 16, hy), self.media_name, fill=mix(TEXT, PANEL, clamp((p - 0.3) / 0.2)), font=UI_S,
                   anchor="lm")
        else:
            d.text((x0 + 16, hy), self.output, fill=mix(PANEL, TEXT, clamp((p - 0.5) / 0.2)), font=UI_S,
                   anchor="lm")
        saved = f"-{self.saved:.1f}s"
        if (g := clamp((p - 0.8) / 0.2)) > 0:
            d.text((x1 - 16, hy), saved, fill=mix(PANEL, GREEN, g), font=MONO_S, anchor="rm")
        d.text((x1 - 16 - CW_S * (len(saved) + 1), hy), _fmt(self.duration - p * self.saved), fill=DIM,
               font=MONO_S, anchor="rm")

        scale = (WAVE_X1 - WAVE_X0) / self.duration

        def at(time: float) -> float:
            return WAVE_X0 + scale * (time - p * self.removed_before(time))

        top, bottom = WAVE_Y - WAVE_H - 3, WAVE_Y + WAVE_H + 3
        d.rounded_rectangle((WAVE_X0 - 8, top - 4, at(self.duration) + 8, bottom + 4), radius=6, fill=TRACK)
        selected = self.selected(t)
        span = None
        if selected:
            span = (self.words[selected[0]].start, self.words[selected[-1]].end)
            d.rectangle((at(span[0]), top, at(span[1]), bottom), fill=SEL)
        for (a, b), marked_at in zip(self.cuts, self.cut_marked_at):
            if t >= marked_at and p < 1:
                d.rectangle((at(a), top, at(b), bottom), fill=mix(RED_BG, TRACK, p))

        playhead = self.playhead(t)
        for (tc, level), cut in zip(self.bars, self.bar_cut):
            h = max(1, round(level * WAVE_H))
            x = round(at(tc))
            if cut is not None and t >= self.cut_marked_at[cut]:
                if p > 0.75:
                    continue
                color, width = mix(RED, TRACK, p), 2 if p < 0.4 else 1
            else:
                width = 2
                if span and span[0] <= tc <= span[1]:
                    color = BLUE
                elif playhead is not None and tc <= playhead:
                    color = WAVE_LIT
                else:
                    color = mix(WAVE, GREEN, p)
            d.rectangle((x - width + 1, WAVE_Y - h, x, WAVE_Y + h), fill=color)
        if playhead is not None:
            x = at(playhead)
            d.line((x, top - 4, x, bottom + 4), fill=BLUE, width=2)


def _complement(keep: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    cuts, prev = [], 0.0
    for a, b in keep:
        if a > prev + 1e-6:
            cuts.append((prev, a))
        prev = b
    if duration > prev + 1e-6:
        cuts.append((prev, duration))
    return cuts


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for i in indices:
        if runs and runs[-1][-1] == i - 1:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def _levels(media: Path, n: int) -> list[tuple[float, float]]:
    """(time, loudness 0..1) for n evenly spaced waveform bars."""
    from faster_whisper import decode_audio

    audio = decode_audio(str(media))  # 16 kHz mono
    edges = np.linspace(0, len(audio), n + 1).astype(int)
    rms = np.array([np.sqrt(np.mean(audio[a:b] ** 2)) for a, b in zip(edges, edges[1:])])
    rms = np.clip(rms / np.percentile(rms, 98), 0, 1) ** 0.7
    step = len(audio) / 16000 / n
    return [((k + 0.5) * step, float(r)) for k, r in enumerate(rms)]


def _cli_summary(media: Path, script: Path) -> str:
    """The summary line `unsaid cut` prints for this edit."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        unsaid_main(["cut", str(media), "--script", str(script), "--dry-run"])
    return next(line for line in err.getvalue().splitlines() if line.startswith("removed "))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    media, script, out = map(Path, args)
    demo = Demo(media, script)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = round(demo.end * FPS)
    with tempfile.TemporaryDirectory() as tmp:
        for f in range(n):
            demo.frame(f / FPS).save(Path(tmp, f"f{f:04d}.png"), compress_level=1)
        frames, palette = str(Path(tmp, "f%04d.png")), str(Path(tmp, "palette.png"))
        ff = [ffmpeg_exe(), "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", frames]
        subprocess.run([*ff, "-vf", "palettegen=max_colors=128:stats_mode=full", palette], check=True)
        subprocess.run([*ff, "-i", palette, "-lavfi", "paletteuse=dither=none:diff_mode=rectangle", str(out)],
                       check=True)
    print(f"{out}: {n} frames, {n / FPS:.1f}s, {out.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
