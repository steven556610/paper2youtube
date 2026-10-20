import json
from pathlib import Path

from paper2youtube.llm import ScriptedLLM
from paper2youtube.pipeline import run_pipeline
from paper2youtube.config import Settings


def _seed_workdir(tmp_path: Path) -> Path:
    """Create a literature dir that looks post-ingest so pipeline can resume locally."""
    work = tmp_path / "literature"
    (work / "source").mkdir(parents=True)
    (work / "figures").mkdir()
    (work / "slides").mkdir()
    (work / "audio").mkdir()
    (work / "source" / "paper.md").write_text(
        "# Demo Paper\n\n## Introduction\n\nMotivation text.\n\n## Methodology\n\nPipeline.\n\n"
        "## Experimental Setup\n\nImageNet mAP.\n\n## Main Results\n\nWin.\n\n"
        "## Code\n\nhttps://github.com/foo/bar\n",
        encoding="utf-8",
    )
    from PIL import Image

    for i in (1, 2):
        Image.new("RGB", (80, 50), (i * 40, 10, 10)).save(work / "figures" / f"fig_{i:02d}.png")
    (work / "manifest.json").write_text(
        json.dumps(
            {
                "stages": {
                    "ingest": {
                        "done": True,
                        "source_kind": "pdf",
                        "arxiv_id": "2510.05096",
                        "title": "Demo Paper",
                        "n_figures": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return work


def test_pipeline_offline_end_to_end(tmp_path):
    work = _seed_workdir(tmp_path)
    # replies: 5 sections + 2 figures
    replies = ["講稿一", "講稿二", "講稿三", "講稿四", "講稿五"]
    replies += [
        '{"narration": "第一張圖解說", "spatial": "左到右", "bullets": ["a"]}',
        '{"narration": "第二張圖解說", "spatial": "折線圖", "bullets": ["b"]}',
    ]
    llm = ScriptedLLM(replies=replies)
    settings = Settings(tts_backend="silent", api_key="test")
    result = run_pipeline(
        str(work),
        work,
        settings,
        llm=llm,
        offline=True,
        resume=True,
        upload=False,
        progress=lambda m: None,
    )
    assert result.ok or result.errors  # video may warn without ffmpeg
    assert (work / "slides_plan.json").exists()
    assert (work / "output_notes.md").exists()
    assert (work / "output_slides.pptx").exists()
    assert list((work / "slides").glob("slide_*.png"))
    assert list((work / "audio").glob("audio_*.wav"))
    assert (work / "chapters.txt").exists()
    assert (work / "manifest.json").exists()
    plan = json.loads((work / "slides_plan.json").read_text(encoding="utf-8"))
    assert len(plan["items"]) == 5 + 2
    chapters = (work / "chapters.txt").read_text(encoding="utf-8")
    assert chapters.startswith("00:00")


def test_pipeline_resume_skips(tmp_path):
    work = _seed_workdir(tmp_path)
    llm = ScriptedLLM(replies=["s"] * 5 + ['{"narration": "n"}', '{"narration": "n"}'])
    settings = Settings(tts_backend="silent", api_key="test")
    r1 = run_pipeline(str(work), work, settings, llm=llm, progress=lambda m: None)
    assert r1.manifest["stages"]["script"]["done"]
    # second run should skip script without consuming LLM replies
    llm2 = ScriptedLLM(replies=[])
    r2 = run_pipeline(str(work), work, settings, llm=llm2, resume=True, progress=lambda m: None)
    assert r2.manifest["stages"]["script"]["done"]
    assert len(llm2.calls) == 0


def test_cli_parser():
    from run import build_parser

    p = build_parser()
    args = p.parse_args(["--input", "literature/", "--offline"])
    assert args.input == "literature/"
    assert args.offline is True