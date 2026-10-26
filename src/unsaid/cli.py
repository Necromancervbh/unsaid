"""unsaid: edit video by editing text."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .cuts import plan_cuts
from .fillers import find_fillers
from .render import RenderError, default_output, probe, render
from .script import removed_by_script
from .transcript import Transcript, load_cached, transcript_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.func(args)
    except (RenderError, FileNotFoundError, ValueError) as e:
        print(f"unsaid: error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def cmd_transcribe(args) -> int:
    t = _get_transcript(args, force=args.force)
    script = script_path(args.input)
    script.write_text(t.to_text(), encoding="utf-8")
    print(f"{len(t.words)} words, {_fmt(t.duration)}, language {t.language}", file=sys.stderr)
    print(f"transcript: {transcript_path(args.input)}", file=sys.stderr)
    print(f"script:     {script}  (delete words here, then: unsaid cut {args.input.name} --script {script.name})",
          file=sys.stderr)
    return 0


def cmd_cut(args) -> int:
    t = _get_transcript(args)
    removed: set[int] = set()
    n_fillers = 0
    if not args.no_fillers:
        fillers = find_fillers(t, aggressive=args.aggressive, extra=tuple(args.filler))
        n_fillers = len(fillers)
        removed |= fillers
    if args.script:
        by_script, changed = removed_by_script(t, args.script.read_text(encoding="utf-8"))
        removed |= by_script
        if changed:
            print(f"note: {changed} words in {args.script.name} differ from the transcript; they were kept "
                  "(unsaid can only remove words, not change them)", file=sys.stderr)

    plan = plan_cuts(t, removed, max_pause=args.max_pause)
    if args.dry_run or args.verbose:
        print(_marked_text(t, removed))
    print(f"removed {len(removed)} words ({n_fillers} fillers), {max(0, len(plan.keep) - 1)} cuts: "
          f"{_fmt(plan.duration)} -> {_fmt(plan.output_duration)} (-{plan.saved:.1f}s)", file=sys.stderr)
    if args.dry_run:
        return 0

    info = probe(args.input)
    output = args.output or default_output(args.input, info.has_video)
    if output.resolve() == args.input.resolve():
        raise ValueError("output would overwrite the input")
    render(args.input, plan.keep, output, crf=args.crf, preset=args.preset, quiet=args.quiet)
    print(output)
    return 0


def cmd_edit(args) -> int:
    from .editor import Session, serve

    t = _get_transcript(args)
    output = args.output or default_output(args.input, probe(args.input).has_video)
    if output.resolve() == args.input.resolve():
        raise ValueError("output would overwrite the input")
    session = Session(args.input, t, output, max_pause=args.max_pause, fillers=not args.no_fillers)
    serve(session, port=args.port, open_browser=not args.no_browser, log=lambda m: print(m, file=sys.stderr))
    return 0


def _get_transcript(args, force: bool = False) -> Transcript:
    if not args.input.exists():
        raise FileNotFoundError(args.input)
    if not force:
        cached = load_cached(args.input)
        if cached is not None:
            return cached
    from .transcribe import transcribe

    print(f"transcribing {args.input.name} with whisper '{args.model}' (first run downloads the model)...",
          file=sys.stderr)
    t = transcribe(args.input, model=args.model, language=args.language, device=args.device,
                   verbatim=not args.no_verbatim, progress=not getattr(args, "quiet", False))
    t.save(transcript_path(args.input))
    return t


def script_path(media: Path) -> Path:
    return media.with_name(media.stem + ".unsaid.txt")


def _marked_text(t: Transcript, removed: set[int]) -> str:
    return " ".join(f"[{w.text}]" if i in removed else w.text for i, w in enumerate(t.words))


def _fmt(seconds: float) -> str:
    m, s = divmod(round(seconds, 1), 60)  # round first, or 239.96 prints as 3:60.0
    return f"{int(m)}:{s:04.1f}"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="unsaid", description=__doc__)
    p.add_argument("--version", action="version", version=f"unsaid {__version__}")
    sub = p.add_subparsers(required=True, metavar="command")

    asr = argparse.ArgumentParser(add_help=False)
    asr.add_argument("input", type=Path, help="video or audio file")
    asr.add_argument("--model", default="small",
                     help="whisper model: tiny, base, small, medium, large-v3, turbo, or a path (default: small)")
    asr.add_argument("--language", help="language code, e.g. en (default: auto-detect)")
    asr.add_argument("--device", default="auto", help="auto, cpu or cuda (default: auto)")
    asr.add_argument("--no-verbatim", action="store_true",
                     help="don't prime whisper to transcribe fillers verbatim")

    t = sub.add_parser("transcribe", parents=[asr], help="transcribe and write an editable script")
    t.add_argument("--force", action="store_true", help="re-transcribe even if a transcript exists")
    t.set_defaults(func=cmd_transcribe)

    e = sub.add_parser("edit", parents=[asr], help="edit the transcript in your browser and export the cut")
    e.add_argument("-o", "--output", type=Path, help="output file (default: <input>.unsaid.mp4)")
    e.add_argument("--no-fillers", action="store_true", help="don't pre-select filler words for cutting")
    e.add_argument("--max-pause", type=float, metavar="SECONDS", help="shorten pauses longer than this")
    e.add_argument("--port", type=int, default=0, help="port to serve on (default: any free port)")
    e.add_argument("--no-browser", action="store_true", help="print the editor's URL instead of opening it")
    e.set_defaults(func=cmd_edit)

    c = sub.add_parser("cut", parents=[asr], help="remove fillers / deleted words and export")
    c.add_argument("-o", "--output", type=Path, help="output file (default: <input>.unsaid.mp4)")
    c.add_argument("--script", type=Path, help="edited script: words deleted from it are cut from the video")
    c.add_argument("--no-fillers", action="store_true", help="don't remove filler words")
    c.add_argument("--aggressive", action="store_true",
                   help="also remove 'like', 'you know', 'I mean', ... when set off by commas")
    c.add_argument("--filler", action="append", default=[], metavar="WORD",
                   help="extra filler word or phrase to remove (repeatable)")
    c.add_argument("--max-pause", type=float, metavar="SECONDS", help="shorten pauses longer than this")
    c.add_argument("--dry-run", action="store_true", help="show what would be cut, don't render")
    c.add_argument("-v", "--verbose", action="store_true", help="print the transcript with cuts marked")
    c.add_argument("-q", "--quiet", action="store_true", help="no progress output")
    c.add_argument("--crf", type=int, default=18, help="x264 quality, lower is better (default: 18)")
    c.add_argument("--preset", default="veryfast", help="x264 preset (default: veryfast)")
    c.set_defaults(func=cmd_cut)
    return p


if __name__ == "__main__":
    sys.exit(main())
