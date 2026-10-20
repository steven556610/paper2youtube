from pathlib import Path

from paper2youtube.code_checker import CodeStatus
from paper2youtube.ingest import Figure, PaperBundle
from paper2youtube.llm import ScriptedLLM
from paper2youtube.script_builder import (
    FIXED_SECTIONS,
    SlideItem,
    SlidesPlan,
    build_plan,
    render_notes,
    write_plan,
)


def _bundle(tmp_path: Path) -> PaperBundle:
    fig = tmp_path / "figures" / "fig_01.png"
    fig.parent.mkdir(parents=True, exist_ok=True)
    fig.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    (tmp_path / "source").mkdir(exist_ok=True)
    return PaperBundle(
        arxiv_id="2510.05096",
        title="Demo",
        abstract="Abstract.",
        markdown="# Demo\n\n## Introduction\n\nPain point here.\n\n## Methodology\n\nWe use a pipeline.\n\n## Experimental Setup\n\nImageNet, mAP.\n\n## Main Results & Ablation\n\nWe win on mAP.\n",
        tex_sources=["main.tex"],
        figures=[
            Figure(
                id="Fig1",
                path="fig_01.png",
                caption="Three-stage pipeline diagram.",
                label="fig:pipeline",
                context=["Figure 1 shows the pipeline stages."],
                local_path=str(fig),
            )
        ],
        code_urls=["https://github.com/foo/bar"],
        source_dir=tmp_path / "source",
        workdir=tmp_path,
        source_kind="arxiv_eprint",
    )


def test_build_plan_structure(tmp_path):
    bundle = _bundle(tmp_path)
    code = CodeStatus(spoken_intro="本篇已開源。", summary="real_code")
    # replies: 5 sections + 1 figure
    replies = [f"講稿-{i}" for i in range(5)] + [
        '{"narration": "這是由左至右的 pipeline 圖。", "spatial": "左到右三階段", "bullets": ["encoder", "decoder"]}'
    ]
    llm = ScriptedLLM(replies=replies)
    plan = build_plan(bundle, code, llm, lang="zh-TW")

    assert len(plan.items) == len(FIXED_SECTIONS) + 1
    kinds = [i.kind for i in plan.items]
    assert kinds[:5] == ["fixed"] * 5
    assert kinds[5] == "figure"
    fig_item = plan.items[5]
    assert fig_item.narration.startswith("這是")
    assert fig_item.figure_path
    assert "figures/fig_01.png" in fig_item.slide_markdown
    assert plan.code_intro == "本篇已開源。"
    assert plan.notes_markdown


def test_build_plan_figure_fallback(tmp_path):
    bundle = _bundle(tmp_path)
    code = CodeStatus(spoken_intro="x")
    replies = ["s1", "s2", "s3", "s4", "s5", "not-json"]
    plan = build_plan(bundle, code, ScriptedLLM(replies=replies))
    fig_item = plan.items[-1]
    assert fig_item.narration  # fallback text filled
    assert fig_item.id.startswith("figure_")


def test_write_plan_and_reload(tmp_path):
    bundle = _bundle(tmp_path)
    code = CodeStatus(spoken_intro="intro")
    replies = ["s1", "s2", "s3", "s4", "s5", '{"narration": "n", "bullets": ["b"]}']
    plan = build_plan(bundle, code, ScriptedLLM(replies=replies))
    plan_path, notes_path = write_plan(plan, tmp_path)
    assert plan_path.exists()
    assert notes_path.exists()
    reloaded = SlidesPlan.from_json(plan_path)
    assert len(reloaded.items) == len(plan.items)
    assert reloaded.paper_title == "Demo"


def test_write_plan_without_notes_uses_plan_only(tmp_path):
    from paper2youtube.script_builder import render_notes_from_plan

    plan = SlidesPlan(
        paper_title="Solo",
        arxiv_id="2510.05096",
        lang="zh-TW",
        code_intro="已開源",
        items=[
            SlideItem(
                id="overview_code",
                kind="fixed",
                title="Overview & Code",
                slide_markdown="# t\n- a\n",
                narration="講稿",
            )
        ],
        notes_markdown="",
    )
    notes = render_notes_from_plan(plan)
    assert "Solo" in notes and "講稿" in notes and "已開源" in notes
    plan_path, notes_path = write_plan(plan, tmp_path)
    assert "Narrated outline" in notes_path.read_text(encoding="utf-8")


def test_render_notes_contains_sections(tmp_path):
    bundle = _bundle(tmp_path)
    code = CodeStatus(spoken_intro="開源說明", summary="real_code")
    replies = ["a", "b", "c", "d", "e", '{"narration": "n", "bullets": []}']
    plan = build_plan(bundle, code, ScriptedLLM(replies=replies))
    notes = render_notes(plan, bundle, code)
    assert "Narrated outline" in notes
    assert "Code availability" in notes