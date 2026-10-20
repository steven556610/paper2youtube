"""Command-line interface for paper2youtube."""

from __future__ import annotations

import argparse
import sys

from .config import load_settings
from .pipeline import STAGES, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paper2youtube",
        description="arXiv/PDF → deep notes + slides + per-figure audio + YouTube video",
    )
    p.add_argument(
        "--input",
        required=True,
        help="arXiv URL/id, PDF path, e-print dir, or literature output dir (e.g. literature/)",
    )
    p.add_argument(
        "--workdir",
        default=None,
        help="Output literature directory. Default: --input if it is a dir, else ./literature/",
    )
    p.add_argument("--stages", default=",".join(STAGES), help=f"Comma list of stages: {', '.join(STAGES)}")
    p.add_argument("--from-stage", default=None, help="Start from this stage through the end")
    p.add_argument("--no-resume", action="store_true", help="Ignore manifest checkpoints")
    p.add_argument("--offline", action="store_true", help="No LLM/TTS network — stubs + silent audio")
    p.add_argument("--upload", action="store_true", help="Upload to YouTube if youtubeuploader exists")
    p.add_argument("--lang", default=None, help="Narration language tag (default zh-TW)")
    p.add_argument("--voice", default=None, help="TTS voice (edge-tts)")
    p.add_argument("--tts-backend", default=None, choices=["edge", "silent", "auto"], help="TTS backend")
    p.add_argument("--model", default=None, help="Override LLM model id")
    p.add_argument("--base-url", default=None, help="Override OpenAI-compatible base URL")
    p.add_argument("--max-figures", type=int, default=None, help="Cap per-figure VLM passes")
    p.add_argument("--version", action="version", version=_version_flag())
    return p


def _version_flag() -> str:
    from . import __version__

    return f"paper2youtube {__version__}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    input_ref = args.input
    workdir = args.workdir
    if not workdir:
        from pathlib import Path

        in_path = Path(input_ref)
        workdir = str(in_path) if in_path.is_dir() else "literature"

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    if args.from_stage:
        if args.from_stage not in STAGES:
            print(f"unknown stage: {args.from_stage}", file=sys.stderr)
            return 2
        start = STAGES.index(args.from_stage)
        stages = STAGES[start:]

    overrides = {}
    if args.lang:
        overrides["lang"] = args.lang
    if args.voice:
        overrides["tts_voice"] = args.voice
    if args.tts_backend:
        overrides["tts_backend"] = args.tts_backend
    if args.model:
        overrides["model"] = args.model
        overrides["small_model"] = args.model
        overrides["vlm_model"] = args.model
    if args.base_url:
        overrides["base_url"] = args.base_url
    if args.max_figures is not None:
        overrides["max_figure_passes"] = args.max_figures
    if args.offline:
        overrides["tts_backend"] = "silent"

    settings = load_settings(**overrides)
    result = run_pipeline(
        input_ref,
        workdir,
        settings,
        stages=stages,
        resume=not args.no_resume,
        upload=args.upload,
        offline=args.offline,
    )

    print(f"\nworkdir: {result.workdir}")
    for stage, info in result.manifest.get("stages", {}).items():
        flag = "OK" if info.get("done") else "WARN"
        print(f"  [{flag}] {stage}: { {k: v for k, v in info.items() if k != 'at'} }")
    if result.errors:
        print("errors:")
        for e in result.errors:
            print(f"  - {e}")
        return 1
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())