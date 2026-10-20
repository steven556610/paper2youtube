from pathlib import Path

from paper2youtube.script_builder import SlideItem, SlidesPlan
from paper2youtube.slides import render_slide_png, render_slides, _md_bullets


def _plan(tmp_path: Path) -> tuple[SlidesPlan, Path]:
    fig = tmp_path / "figures" / "f1.png"
    fig.parent.mkdir(parents=True, exist_ok=True)
    # minimal valid PNG via PIL
    from PIL import Image

    Image.new("RGB", (200, 120), (200, 40, 40)).save(fig)
    item = SlideItem(
        id="figure_01",
        kind="figure",
        title="Fig.1: pipeline",
        slide_markdown="# Fig.1\n![x](figures/f1.png)\n- stage A\n- stage B\n",
        narration="narration",
        figure_path=str(fig),
    )
    fixed = SlideItem(
        id="motivation",
        kind="fixed",
        title="Motivation",
        slide_markdown="# Motivation\n- pain one\n- pain two\n",
        narration="narr",
    )
    plan = SlidesPlan(
        paper_title="Demo",
        arxiv_id="2510.05096",
        lang="zh-TW",
        code_intro="intro",
        items=[fixed, item],
    )
    return plan, fig


def test_md_bullets():
    md = "# T\n- **bold** one\n- two\n![img](x.png)\n*cap*\n"
    bullets = _md_bullets(md)
    assert "bold one" in bullets
    assert "two" in bullets
    assert not any(b.startswith("![") for b in bullets)


def test_render_slide_png(tmp_path):
    out = tmp_path / "slide_01.png"
    render_slide_png("# T\n- a\n- b\n", "Hello", out)
    assert out.exists()
    assert out.stat().st_size > 1000


def test_render_slides_outputs(tmp_path):
    plan, _ = _plan(tmp_path)
    pngs = render_slides(plan, tmp_path)
    assert len(pngs) == 2
    assert (tmp_path / "output_slides.pptx").exists()
    assert (tmp_path / "slides" / "slide_01.png").exists()
    assert (tmp_path / "slides" / "slide_02.png").exists()