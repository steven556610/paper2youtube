from pathlib import Path

import pytest

from paper2youtube.script_builder import SlideItem, SlidesPlan
from paper2youtube.tts import AudioClip, write_silent_wav
from paper2youtube.video import (
    Chapter,
    build_chapters,
    format_youtube_description,
    render_video,
    write_chapters,
    write_video_manifest,
)


def _fixture(tmp_path: Path):
    slides = []
    clips = []
    items = []
    for i in (1, 2):
        sp = tmp_path / "slides" / f"slide_{i:02d}.png"
        sp.parent.mkdir(parents=True, exist_ok=True)
        from PIL import Image

        Image.new("RGB", (640, 360), (10, 20, 30)).save(sp)
        slides.append(sp)
        ap = write_silent_wav(tmp_path / "audio" / f"audio_{i:02d}.wav", 1.0 if i == 1 else 1.5)
        clips.append(AudioClip(index=i, path=ap, duration_sec=1.0 if i == 1 else 1.5, text="t"))
        items.append(
            SlideItem(
                id=f"id{i}",
                kind="fixed",
                title=f"Motivation {i}" if i == 1 else "Fig.1 pipeline",
                slide_markdown="# x\n- y\n",
                narration="n",
            )
        )
    plan = SlidesPlan(paper_title="Demo", arxiv_id="2510.05096", lang="zh-TW", code_intro="code ok", items=items)
    return plan, slides, clips


def test_build_chapters_and_format(tmp_path):
    plan, slides, clips = _fixture(tmp_path)
    chapters = build_chapters(plan, clips)
    assert chapters[0].timestamp() == "00:00"
    assert abs(chapters[1].start_sec - 1.0) < 0.05
    assert chapters[1].timestamp() == "00:01"
    desc = format_youtube_description(plan, chapters)
    assert "00:00" in desc
    assert "arxiv.org/abs/2510.05096" in desc
    assert "Motivation 1" in desc


def test_write_chapters(tmp_path):
    ch = [Chapter(0.0, "Intro"), Chapter(65.0, "Method")]
    assert ch[1].timestamp() == "01:05"
    p = write_chapters(ch, tmp_path / "chapters.txt")
    text = p.read_text(encoding="utf-8")
    assert "00:00 Intro" in text
    assert "01:05 Method" in text


def test_write_video_manifest(tmp_path):
    plan, slides, clips = _fixture(tmp_path)
    chapters = build_chapters(plan, clips)
    p = write_video_manifest(tmp_path, slides=slides, clips=clips, chapters=chapters, video_path=None)
    assert p.exists()
    assert "chapters" in p.read_text(encoding="utf-8")


def test_render_video_requires_ffmpeg(tmp_path):
    plan, slides, clips = _fixture(tmp_path)
    import shutil

    if shutil.which("ffmpeg"):
        out = render_video(slides, clips, tmp_path / "output_video.mp4")
        assert out.exists() and out.stat().st_size > 0
    else:
        with pytest.raises(RuntimeError, match="ffmpeg"):
            render_video(slides, clips, tmp_path / "output_video.mp4")