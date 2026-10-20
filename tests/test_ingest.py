import tarfile
from pathlib import Path

from paper2youtube.ingest import (
    detect_code_urls,
    ingest,
    parse_arxiv_id,
    resolve_input_kind,
    tex_to_markdown,
    _strip_tex,
)


def test_parse_arxiv_id():
    assert parse_arxiv_id("2510.05096") == "2510.05096"
    assert parse_arxiv_id("https://arxiv.org/abs/2510.05096") == "2510.05096"
    assert parse_arxiv_id("https://arxiv.org/pdf/2510.05096v2") == "2510.05096v2"
    assert parse_arxiv_id("https://example.com/foo") is None


def test_resolve_input_kind(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert resolve_input_kind("https://arxiv.org/abs/2510.05096") == "arxiv"
    assert resolve_input_kind(str(pdf)) == "pdf"
    assert resolve_input_kind(str(tmp_path)) == "dir"


def test_strip_tex():
    assert "bold" in _strip_tex(r"\textbf{bold} and \ref{fig:a}")
    assert "\\textbf" not in _strip_tex(r"\textbf{bold}")


def test_tex_to_markdown_sections():
    tex = r"""
\title{My Paper}
\begin{abstract}We do things.\end{abstract}
\section{Introduction}
Hello world.
\section{Method}
Our method is great.
"""
    md = tex_to_markdown(tex, title="My Paper", abstract="We do things.")
    assert "# My Paper" in md
    assert "## Introduction" in md
    assert "## Method" in md
    assert "Hello world" in md


def _make_eprint(tmp_path: Path) -> Path:
    root = tmp_path / "ep"
    root.mkdir()
    (root / "main.tex").write_text(
        r"""
\documentclass{article}
\title{Demo Paper}
\begin{abstract}Abstract text here.\end{abstract}
\section{Introduction}
See \ref{fig:pipeline} for the system.
\begin{figure}
\includegraphics[width=\linewidth]{figs/pipeline}
\caption{Three-stage pipeline.}
\label{fig:pipeline}
\end{figure}
\section{Experiments}
We use ResNet and report mAP.
\end{document}
""",
        encoding="utf-8",
    )
    figs = root / "figs"
    figs.mkdir()
    (figs / "pipeline.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    tar_path = tmp_path / "ep.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(root, arcname=".")
    return tar_path


def test_ingest_arxiv_tarball(tmp_path, monkeypatch):
    tar_path = _make_eprint(tmp_path)

    def fake_download(url, dest, timeout=60.0):
        dest.write_bytes(tar_path.read_bytes())

    import paper2youtube.ingest as ing

    monkeypatch.setattr(ing, "_download", fake_download)
    work = tmp_path / "lit"
    bundle = ing.ingest_arxiv("2510.05096", work)
    assert bundle.source_kind == "arxiv_eprint"
    assert bundle.arxiv_id == "2510.05096"
    assert "Demo Paper" in bundle.title or bundle.title
    figs = bundle.figures
    assert len(figs) >= 1
    assert any(f.local_path and Path(f.local_path).exists() for f in figs)
    # context from \ref{fig:pipeline}
    pipeline_figs = [f for f in figs if "pipeline" in (f.label or "") or "pipeline" in (f.path or "")]
    assert pipeline_figs
    assert pipeline_figs[0].context
    assert (work / "source" / "paper.md").exists()
    assert (work / "figures").exists()


def test_ingest_dir_from_extracted(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "paper.tex").write_text(
        r"""
\title{Dir Paper}
\section{Intro}
Text with \ref{fig:a}.
\begin{figure}\includegraphics{a}\caption{Cap A}\label{fig:a}\end{figure}
""",
        encoding="utf-8",
    )
    (root / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    work = tmp_path / "out"
    bundle = ingest(str(root), work)
    assert bundle.figures
    assert bundle.figures[0].local_path


def test_detect_code_urls():
    text = "Code at https://github.com/foo/bar and paper https://huggingface.co/papers/2510.05096"
    urls = detect_code_urls(text)
    assert any("github.com/foo/bar" in u for u in urls)


def test_discover_local_figures(tmp_path):
    from paper2youtube.ingest import discover_local_figures

    fig_dir = tmp_path / "figures"
    fig_dir.mkdir()
    (fig_dir / "fig_01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (fig_dir / "table_01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (fig_dir / "notes.txt").write_text("x")
    figs = discover_local_figures(tmp_path)
    assert len(figs) == 2
    kinds = {f.kind for f in figs}
    assert "figure" in kinds and "table" in kinds


def test_ingest_text_picks_up_workdir_figures(tmp_path):
    work = tmp_path / "lit"
    (work / "source").mkdir(parents=True)
    (work / "figures").mkdir()
    (work / "source" / "paper.md").write_text("# Demo\n\n## Intro\n\nHello.\n", encoding="utf-8")
    (work / "figures" / "fig_01.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    bundle = ingest(str(work / "source" / "paper.md"), work)
    assert bundle.figures
    assert bundle.figures[0].local_path