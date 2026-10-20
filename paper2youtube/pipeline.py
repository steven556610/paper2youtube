"""Orchestrate stages 1–6 with checkpoint/resume via manifest.json."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .code_checker import check_code, code_status_to_md
from .config import Settings, ensure_workdir
from .ingest import ingest
from .llm import LLMClient, ScriptedLLM
from .script_builder import build_plan, write_plan
from .slides import render_slides
from .tts import export_full_audio, synthesize_plan
from .video import (
    build_chapters,
    format_youtube_description,
    render_video,
    upload_youtube,
    write_chapters,
    write_video_manifest,
)

STAGES = ["ingest", "code_check", "script", "slides", "tts", "video"]


@dataclass
class PipelineResult:
    workdir: Path
    manifest: dict = field(default_factory=dict)
    ok: bool = True
    errors: list[str] = field(default_factory=list)


def run_pipeline(
    input_ref: str,
    workdir: str | Path,
    settings: Settings | None = None,
    *,
    llm: Any | None = None,
    stages: list[str] | None = None,
    resume: bool = True,
    upload: bool = False,
    offline: bool = False,
    progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    """
    Run the modular pipeline.

    input_ref: arXiv URL/id, PDF path, or existing literature dir with sources.
    workdir:   output literature directory (created if needed).
    """
    settings = settings or Settings()
    workdir = ensure_workdir(workdir)
    say = progress or (lambda msg: print(msg, flush=True))
    manifest_path = workdir / "manifest.json"
    manifest = _load_manifest(manifest_path) if resume else {}
    manifest.setdefault("stages", {})
    manifest["input"] = input_ref
    manifest["updated_at"] = _now()
    result = PipelineResult(workdir=workdir, manifest=manifest)

    wanted = stages or STAGES
    if llm is None:
        if offline:
            llm = ScriptedLLM()
        else:
            llm = LLMClient(settings)

    bundle = None
    code_status = None
    plan = None
    clips: list = []
    slides: list[Path] = []
    chapters: list = []

    try:
        # ---- 1. ingest ----
        if "ingest" in wanted:
            say("[1/6] ingest — parse arXiv source / PDF")
            if resume and manifest["stages"].get("ingest", {}).get("done") and (workdir / "source" / "paper.md").exists():
                say("      skip (checkpoint)")
            else:
                # If input is already this workdir, re-ingest from local tree/pdf
                src = input_ref
                if Path(str(input_ref)).resolve() == workdir.resolve():
                    local_pdf = list((workdir / "source").glob("*.pdf"))
                    local_tree = workdir / "source" / "tree"
                    if local_tree.is_dir():
                        src = str(local_tree)
                    elif local_pdf:
                        src = str(local_pdf[0])
                    else:
                        md = workdir / "source" / "paper.md"
                        if md.exists():
                            src = str(md)
                bundle = ingest(src, workdir, timeout=settings.request_timeout)
                manifest["stages"]["ingest"] = {
                    "done": True,
                    "source_kind": bundle.source_kind,
                    "arxiv_id": bundle.arxiv_id,
                    "title": bundle.title,
                    "n_figures": len(bundle.figures),
                    "at": _now(),
                }
                _save_manifest(manifest_path, manifest)
            say(f"      figures: {manifest['stages']['ingest'].get('n_figures', 0)}")

        # ---- 2. code check ----
        if "code_check" in wanted:
            say("[2/6] code_check — GitHub / HF paper API")
            if resume and manifest["stages"].get("code_check", {}).get("done") and (workdir / "code_status.json").exists():
                say("      skip (checkpoint)")
                code_status = json.loads((workdir / "code_status.json").read_text(encoding="utf-8"))
                from .code_checker import CodeStatus, RepoReport  # local restore
                code_status = _code_status_from_dict(code_status)
            else:
                md = _read_markdown(workdir)
                code_status = check_code(
                    md,
                    arxiv_id=manifest["stages"].get("ingest", {}).get("arxiv_id"),
                    token=settings.github_token,
                    timeout=min(settings.request_timeout, 30.0),
                    inspect=not offline,
                )
                (workdir / "code_status.json").write_text(
                    json.dumps(code_status.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                (workdir / "code_status.md").write_text(code_status_to_md(code_status), encoding="utf-8")
                manifest["stages"]["code_check"] = {
                    "done": True,
                    "summary": code_status.summary,
                    "at": _now(),
                }
                _save_manifest(manifest_path, manifest)
            say(f"      summary: {manifest['stages']['code_check'].get('summary')}")

        # ---- 3. script (notes + slides_plan.json) ----
        if "script" in wanted:
            say("[3/6] script — 5 fixed nodes + per-figure VLM pass")
            plan_path = workdir / "slides_plan.json"
            notes_path = workdir / "output_notes.md"
            if resume and plan_path.exists() and notes_path.exists() and manifest["stages"].get("script", {}).get("done"):
                say("      skip (checkpoint)")
                from .script_builder import SlidesPlan
                plan = SlidesPlan.from_json(plan_path)
            else:
                bundle = bundle or _reload_bundle(workdir)
                code_status = code_status or _code_status_from_dict(
                    json.loads((workdir / "code_status.json").read_text(encoding="utf-8"))
                    if (workdir / "code_status.json").exists()
                    else {}
                )
                plan = build_plan(
                    bundle,
                    code_status,
                    llm,
                    lang=settings.lang,
                    max_figures=settings.max_figure_passes,
                )
                write_plan(plan, workdir)
                manifest["stages"]["script"] = {
                    "done": True,
                    "n_items": len(plan.items),
                    "at": _now(),
                }
                _save_manifest(manifest_path, manifest)
            say(f"      items: {manifest['stages']['script'].get('n_items')}")

        # ---- 4. slides ----
        if "slides" in wanted:
            say("[4/6] slides — PPTX + PNG sequence")
            plan = plan or _load_plan(workdir)
            if resume and manifest["stages"].get("slides", {}).get("done") and (workdir / "output_slides.pptx").exists():
                say("      skip (checkpoint)")
                slides = sorted((workdir / "slides").glob("slide_*.png"))
            else:
                slides = render_slides(plan, workdir)
                manifest["stages"]["slides"] = {
                    "done": True,
                    "n_slides": len(slides),
                    "pptx": "output_slides.pptx",
                    "at": _now(),
                }
                _save_manifest(manifest_path, manifest)
            say(f"      slides: {len(slides)}")

        # ---- 5. tts ----
        if "tts" in wanted:
            say("[5/6] tts — per-slide audio + full track")
            plan = plan or _load_plan(workdir)
            if resume and manifest["stages"].get("tts", {}).get("done") and (workdir / "audio").glob("audio_*.wav"):
                existing = sorted((workdir / "audio").glob("audio_*.wav"))
                if existing:
                    say("      skip (checkpoint)")
                    from .tts import AudioClip, wav_duration
                    clips = [
                        AudioClip(index=i + 1, path=p, duration_sec=wav_duration(p), text="")
                        for i, p in enumerate(existing)
                    ]
            if not clips:
                clips = synthesize_plan(
                    plan,
                    workdir,
                    voice=settings.tts_voice,
                    backend=settings.tts_backend if not offline else "silent",
                )
                full = export_full_audio(clips, workdir)
                manifest["stages"]["tts"] = {
                    "done": True,
                    "n_clips": len(clips),
                    "full_audio": full.name,
                    "at": _now(),
                }
                _save_manifest(manifest_path, manifest)
            say(f"      clips: {len(clips)}")

        # ---- 6. video + chapters ----
        if "video" in wanted:
            say("[6/6] video — bind slide duration = audio duration")
            plan = plan or _load_plan(workdir)
            if not slides:
                slides = sorted((workdir / "slides").glob("slide_*.png"))
            if not clips:
                from .tts import AudioClip, wav_duration
                existing = sorted((workdir / "audio").glob("audio_*.wav"))
                clips = [
                    AudioClip(index=i + 1, path=p, duration_sec=wav_duration(p), text="")
                    for i, p in enumerate(existing)
                ]
            chapters = build_chapters(plan, clips)
            desc = format_youtube_description(plan, chapters)
            write_chapters(chapters, workdir / "chapters.txt", description=desc)
            (workdir / "youtube_description.txt").write_text(desc, encoding="utf-8")

            video_path = workdir / "output_video.mp4"
            produced = None
            if resume and video_path.exists() and manifest["stages"].get("video", {}).get("done"):
                say("      skip (checkpoint)")
                produced = video_path
            else:
                try:
                    produced = render_video(slides, clips, video_path, chapters=chapters)
                    manifest["stages"]["video"] = {
                        "done": True,
                        "video": produced.name,
                        "uploaded": False,
                        "at": _now(),
                    }
                except RuntimeError as exc:
                    result.errors.append(str(exc))
                    say(f"      WARN: {exc}")
                    manifest["stages"]["video"] = {
                        "done": False,
                        "error": str(exc),
                        "chapters": "chapters.txt",
                        "at": _now(),
                    }
                _save_manifest(manifest_path, manifest)

            write_video_manifest(workdir, slides=slides, clips=clips, chapters=chapters, video_path=produced)

            if upload and produced and produced.exists():
                say("      upload → YouTube (unlisted)")
                url = upload_youtube(
                    produced,
                    title=plan.paper_title or workdir.name,
                    description=desc,
                    privacy="unlisted",
                )
                if url:
                    manifest["stages"]["video"]["uploaded"] = True
                    manifest["stages"]["video"]["youtube_url"] = url
                    (workdir / "youtube_url.txt").write_text(url + "\n", encoding="utf-8")
                    say(f"      uploaded: {url}")
                else:
                    result.errors.append("YouTube upload skipped (youtubeuploader not available)")
                _save_manifest(manifest_path, manifest)

    except Exception as exc:
        result.ok = False
        result.errors.append(f"{exc}")
        manifest["last_error"] = {"error": str(exc), "trace": traceback.format_exc()[-2000:], "at": _now()}
        _save_manifest(manifest_path, manifest)
        say(f"ERROR: {exc}")

    manifest["updated_at"] = _now()
    _save_manifest(manifest_path, manifest)
    result.manifest = manifest
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_markdown(workdir: Path) -> str:
    p = workdir / "source" / "paper.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_plan(workdir: Path):
    from .script_builder import SlidesPlan
    return SlidesPlan.from_json(workdir / "slides_plan.json")


def _reload_bundle(workdir: Path):
    from .ingest import PaperBundle, Figure
    md = _read_markdown(workdir)
    meta = {}
    mf = workdir / "manifest.json"
    if mf.exists():
        meta = json.loads(mf.read_text(encoding="utf-8")).get("stages", {}).get("ingest", {})
    figures = []
    fig_dir = workdir / "figures"
    if fig_dir.is_dir():
        for i, p in enumerate(sorted(fig_dir.iterdir()), 1):
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".pdf", ".webp"}:
                figures.append(
                    Figure(
                        id=f"Fig{i}",
                        path=p.name,
                        caption=p.stem,
                        kind="table" if "table" in p.name.lower() else "figure",
                        local_path=str(p),
                    )
                )
    return PaperBundle(
        arxiv_id=meta.get("arxiv_id"),
        title=meta.get("title") or "paper",
        abstract="",
        markdown=md,
        tex_sources=[],
        figures=figures,
        code_urls=[],
        source_dir=workdir / "source",
        workdir=workdir,
        source_kind=meta.get("source_kind", "unknown"),
    )


def _code_status_from_dict(data: dict):
    from .code_checker import CodeStatus, RepoReport
    repos = [RepoReport(**r) for r in data.get("repos", [])]
    return CodeStatus(
        urls=data.get("urls", []),
        repos=repos,
        placeholder_text=bool(data.get("placeholder_text")),
        summary=data.get("summary", ""),
        spoken_intro=data.get("spoken_intro", ""),
    )