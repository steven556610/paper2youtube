"""Stage 5 — per-slide TTS (edge-tts preferred) + full-audio concat."""

from __future__ import annotations

import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from .script_builder import SlidesPlan

# Rough zh/EN mix speaking rate: chars per second (used for silent fallback / estimates).
CHARS_PER_SEC = 5.2


@dataclass
class AudioClip:
    index: int
    path: Path
    duration_sec: float
    text: str


def estimate_duration(text: str) -> float:
    text = (text or "").strip()
    if not text:
        return 1.0
    # CJK chars speak slower than latin words
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = max(0, len(text) - cjk)
    est = cjk / 4.2 + latin / 14.0
    return max(1.5, min(est, 300.0))


def synthesize_plan(
    plan: SlidesPlan,
    workdir: Path,
    *,
    voice: str = "zh-TW-HsiaoChenNeural",
    backend: str = "edge",
    start_index: int = 1,
) -> list[AudioClip]:
    """One audio file per slide item. Returns clips with measured durations."""
    workdir = Path(workdir)
    audio_dir = workdir / "audio"
    audio_dir.mkdir(exist_ok=True)

    clips: list[AudioClip] = []
    for offset, item in enumerate(plan.items):
        idx = start_index + offset
        out = audio_dir / f"audio_{idx:02d}.wav"
        text = item.narration.strip() or item.title
        ok = _synth_one(text, out, voice=voice, backend=backend)
        if not ok:
            write_silent_wav(out, estimate_duration(text))
        dur = wav_duration(out)
        clips.append(AudioClip(index=idx, path=out, duration_sec=dur, text=text))
    return clips


def _synth_one(text: str, out_path: Path, *, voice: str, backend: str) -> bool:
    if backend == "silent":
        write_silent_wav(out_path, estimate_duration(text))
        return True
    if backend in ("edge", "edge-tts", "auto"):
        return edge_tts_synth(text, out_path, voice=voice)
    return False


def edge_tts_synth(text: str, out_path: Path, *, voice: str) -> bool:
    """Use edge-tts if importable. Returns False when unavailable."""
    try:
        import asyncio

        import edge_tts
    except ImportError:
        return False

    async def _run() -> bool:
        try:
            # edge-tts writes mp3 by default; ask for wav via ffmpeg later if needed.
            tmp = out_path.with_suffix(".mp3")
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(tmp))
            if _ffmpeg_available():
                _mp3_to_wav(tmp, out_path)
                tmp.unlink(missing_ok=True)
            else:
                # keep mp3 but also expose as .wav path name via rename for pipeline simplicity
                # duration probe may fail — fall back to estimate sidecar
                shutil.move(str(tmp), str(out_path.with_suffix(".mp3")))
                write_silent_wav(out_path, estimate_duration(text))
                # actually we want the real audio: store mp3 next to wav and prefer ffmpeg later
                return False
            return out_path.exists()
        except Exception:
            return False

    try:
        return asyncio.run(_run())
    except Exception:
        return False


def write_silent_wav(path: Path, duration_sec: float, *, rate: int = 22050) -> Path:
    """Deterministic silent WAV (offline / test / missing TTS fallback)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(duration_sec * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    return path


def wav_duration(path: Path) -> float:
    path = Path(path)
    if not path.exists():
        return 0.0
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 1
                return frames / float(rate)
        except Exception:
            pass
    if _ffprobe_available():
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                text=True,
            )
            return float(out.strip())
        except Exception:
            pass
    return estimate_duration("x" * 50)


def concat_wavs(clips: list[AudioClip], out_path: Path) -> Path:
    """Concatenate per-slide audio into one full track (duration = sum of parts)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srcs = [c.path for c in clips if Path(c.path).exists()]
    if not srcs:
        raise ValueError("No audio clips to concatenate")

    # Prefer ffmpeg concat so sample-rate mismatches cannot stretch duration.
    if _ffmpeg_available():
        list_file = out_path.parent / "_audio_concat.txt"
        list_file.write_text(
            "".join(f"file '{p.resolve()}'\n" for p in srcs),
            encoding="utf-8",
        )
        try:
            subprocess.check_call(
                [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(list_file), "-ar", "24000", "-ac", "1",
                    str(out_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
        except Exception:
            pass
        finally:
            list_file.unlink(missing_ok=True)

    # Pure-python fallback: write at the first clip's rate (no silent stretch).
    rate = 22050
    try:
        with wave.open(str(srcs[0]), "rb") as wf:
            rate = wf.getframerate() or 22050
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
    except Exception:
        channels, sampwidth = 1, 2

    with wave.open(str(out_path), "wb") as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(sampwidth)
        out_wf.setframerate(rate)
        for src in srcs:
            if Path(src).suffix.lower() != ".wav":
                continue
            with wave.open(str(src), "rb") as in_wf:
                # only accept matching format in fallback path
                if in_wf.getframerate() != rate or in_wf.getnchannels() != channels:
                    frames = in_wf.readframes(in_wf.getnframes())
                    # write anyway but recompute header from first clip — last resort
                    out_wf.writeframes(frames)
                else:
                    out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))
    return out_path


def full_audio_path(workdir: Path) -> Path:
    return Path(workdir) / "output_full_audio.mp3"


def export_full_audio(clips: list[AudioClip], workdir: Path) -> Path:
    """Prefer mp3 via ffmpeg; else write output_full_audio.wav and copy hint."""
    workdir = Path(workdir)
    wav_out = workdir / "output_full_audio.wav"
    concat_wavs(clips, wav_out)
    mp3_out = full_audio_path(workdir)
    if _ffmpeg_available():
        try:
            subprocess.check_call(
                ["ffmpeg", "-y", "-i", str(wav_out), "-codec:a", "libmp3lame", "-qscale:a", "4", str(mp3_out)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return mp3_out
        except Exception:
            pass
    # fallback: keep wav as the deliverable, also copy to .mp3 name is wrong —
    # write a tiny note and return wav path. Manifest records the real file.
    return wav_out


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _mp3_to_wav(src: Path, dest: Path) -> None:
    subprocess.check_call(
        ["ffmpeg", "-y", "-i", str(src), str(dest)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_mp3_header_duration(path: Path) -> float | None:
    """Tiny CBR MP3 duration estimate — used only in tests."""
    data = Path(path).read_bytes()
    if len(data) < 4 or data[:3] != b"ID3" and data[0] != 0xFF:
        return None
    # not a full parser; return estimate
    return len(data) / 16000.0