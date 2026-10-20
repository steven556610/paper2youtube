from pathlib import Path

from paper2youtube.script_builder import SlideItem, SlidesPlan
from paper2youtube.tts import (
    concat_wavs,
    estimate_duration,
    export_full_audio,
    synthesize_plan,
    wav_duration,
    write_silent_wav,
)


def _plan(n=2) -> SlidesPlan:
    items = [
        SlideItem(
            id=f"s{i}",
            kind="fixed",
            title=f"T{i}",
            slide_markdown=f"# T{i}\n- body {i}\n",
            narration=f"這是第 {i} 段講稿，大約十幾個字用來估時長。" * 2,
        )
        for i in range(1, n + 1)
    ]
    return SlidesPlan(paper_title="Demo", arxiv_id=None, lang="zh-TW", code_intro="x", items=items)


def test_estimate_duration_bounds():
    assert estimate_duration("") == 1.0
    assert estimate_duration("短") >= 1.5
    assert estimate_duration("a" * 5000) <= 300.0


def test_write_silent_wav_duration(tmp_path):
    p = write_silent_wav(tmp_path / "a.wav", 2.0)
    dur = wav_duration(p)
    assert 1.8 <= dur <= 2.2


def test_synthesize_plan_silent_backend(tmp_path):
    plan = _plan(3)
    clips = synthesize_plan(plan, tmp_path, backend="silent")
    assert len(clips) == 3
    for c in clips:
        assert c.path.exists()
        assert c.duration_sec > 0.5


def test_concat_and_export_full_audio(tmp_path):
    plan = _plan(2)
    clips = synthesize_plan(plan, tmp_path, backend="silent")
    full = export_full_audio(clips, tmp_path)
    assert full.exists()
    total = sum(c.duration_sec for c in clips)
    got = wav_duration(full) if full.suffix == ".wav" else total
    assert got >= total * 0.5


def test_concat_wavs(tmp_path):
    a = write_silent_wav(tmp_path / "a.wav", 1.0)
    b = write_silent_wav(tmp_path / "b.wav", 1.5)
    from paper2youtube.tts import AudioClip

    clips = [
        AudioClip(index=1, path=a, duration_sec=1.0, text="a"),
        AudioClip(index=2, path=b, duration_sec=1.5, text="b"),
    ]
    out = concat_wavs(clips, tmp_path / "full.wav")
    assert 2.3 <= wav_duration(out) <= 2.7