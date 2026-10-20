"""Stage 6 — bind each slide PNG to its audio duration → mp4 + YouTube chapters."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .script_builder import SlidesPlan
from .tts import AudioClip


@dataclass
class Chapter:
    start_sec: float
    title: str

    def timestamp(self) -> str:
        total = int(round(self.start_sec))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


def build_chapters(plan: SlidesPlan, clips: list[AudioClip]) -> list[Chapter]:
    chapters: list[Chapter] = []
    t = 0.0
    n = max(len(plan.items), len(clips))
    for i in range(n):
        title = plan.items[i].title if i < len(plan.items) else f"Section {i + 1}"
        chapters.append(Chapter(start_sec=t, title=_chapter_title(title)))
        if i < len(clips):
            t += max(clips[i].duration_sec, 0.2)
    return chapters


def _chapter_title(title: str) -> str:
    # YouTube chapters must be single-line and short-ish
    t = " ".join((title or "Section").split())
    return t[:80]


def format_youtube_description(plan: SlidesPlan, chapters: list[Chapter], *, arxiv_id: str | None = None) -> str:
    lines = []
    if plan.paper_title:
        lines.append(plan.paper_title)
        lines.append("")
    if arxiv_id or plan.arxiv_id:
        lines.append(f"arXiv: https://arxiv.org/abs/{arxiv_id or plan.arxiv_id}")
        lines.append("")
    lines.append("Chapters")
    lines.append("")
    for ch in chapters:
        lines.append(f"{ch.timestamp()} {ch.title}")
    if plan.code_intro:
        lines += ["", "Code", plan.code_intro]
    return "\n".join(lines) + "\n"


def write_chapters(chapters: list[Chapter], out_path: Path, description: str | None = None) -> Path:
    """Write pure `00:00 Title` lines. Full YouTube description is a separate file."""
    out_path = Path(out_path)
    lines = [f"{ch.timestamp()} {ch.title}" for ch in chapters]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def render_video(
    slides: list[Path],
    clips: list[AudioClip],
    out_path: Path,
    *,
    chapters: list[Chapter] | None = None,
) -> Path:
    """
    slide_i + audio_i → seamless mp4.
    Each slide shows exactly as long as its audio track (100% alignment).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = min(len(slides), len(clips))
    if n == 0:
        raise ValueError("Need at least one slide and one audio clip")

    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            "ffmpeg not found. Install ffmpeg to produce output_video.mp4. "
            "Per-slide PNG/WAV and chapters.txt are still valid deliverables."
        )

    segment_paths: list[Path] = []
    for i in range(n):
        seg = out_path.parent / f"_seg_{i:02d}.mp4"
        dur = max(clips[i].duration_sec, 0.3)
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(slides[i]),
            "-i", str(clips[i].path),
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            "-t", f"{dur:.3f}",
            "-shortest",
            str(seg),
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        segment_paths.append(seg)

    if len(segment_paths) == 1:
        shutil.move(str(segment_paths[0]), str(out_path))
    else:
        list_file = out_path.parent / "_concat.txt"
        list_file.write_text(
            "".join(f"file '{p.resolve()}'\n" for p in segment_paths),
            encoding="utf-8",
        )
        subprocess.check_call(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        list_file.unlink(missing_ok=True)
        for p in segment_paths:
            p.unlink(missing_ok=True)

    return out_path


def upload_youtube(
    video_path: Path,
    *,
    title: str,
    description: str,
    privacy: str = "unlisted",
    playlist: str | None = None,
    uploader: str | None = None,
) -> str | None:
    """Optional upload via porjo/youtubeuploader if installed. Returns video URL or None."""
    exe = uploader or shutil.which("youtubeuploader")
    if not exe or not Path(video_path).exists():
        return None
    cmd = [
        exe,
        "-filename", str(video_path),
        "-title", title,
        "-description", description,
        "-privacy", privacy,
    ]
    if playlist:
        cmd += ["-playlist", playlist]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
    except Exception:
        return None
    for line in out.splitlines():
        if "youtube.com/watch" in line:
            return line.strip()
    return None


def write_video_manifest(
    workdir: Path,
    *,
    slides: list[Path],
    clips: list[AudioClip],
    chapters: list[Chapter],
    video_path: Path | None,
) -> Path:
    workdir = Path(workdir)
    payload = {
        "slides": [str(p.name) for p in slides],
        "clips": [
            {"index": c.index, "file": c.path.name, "duration_sec": round(c.duration_sec, 3)}
            for c in clips
        ],
        "chapters": [{"start_sec": ch.start_sec, "ts": ch.timestamp(), "title": ch.title} for ch in chapters],
        "video": video_path.name if video_path and Path(video_path).exists() else None,
    }
    path = workdir / "video_timeline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path