"""Stage 1 — paper ingest: arXiv e-print source first, PDF fallback."""

from __future__ import annotations

import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ARXIV_ID_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|e-print)/)?(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)",
    re.I,
)
ARXIV_URL_RE = re.compile(r"https?://arxiv\.org/(?:abs|pdf|e-print)/(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)", re.I)

INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{(?P<path>[^}]+)\}",
)
LABEL_RE = re.compile(r"\\label\{(?P<label>[^}]+)\}")
REF_RE = re.compile(r"\\(?:ref|autoref|cref|Cref|eqref)\{(?P<label>[^}]+)\}")
CAPTION_RE = re.compile(r"\\caption\{(?P<cap>.*?)\}\s*(?:\\label\{(?P<label>[^}]+)\})?", re.S)
BEGIN_FIG_RE = re.compile(r"\\begin\{figure\*?\}(?P<body>.*?)\\end\{figure\*?\}", re.S)
BEGIN_TBL_RE = re.compile(r"\\begin\{table\*?\}(?P<body>.*?)\\end\{table\*?\}", re.S)
SECTION_RE = re.compile(r"\\(?:section|subsection|chapter)\*?\{(?P<title>[^}]+)\}")
INPUT_RE = re.compile(r"\\(?:input|include)\{(?P<path>[^}]+)\}")
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)", re.I)
HF_PAPER_RE = re.compile(r"huggingface\.co/papers/(?P<id>\d{4}\.\d{4,5})", re.I)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg", ".gif", ".webp"}


@dataclass
class Figure:
    id: str
    path: str | None
    caption: str = ""
    label: str = ""
    kind: str = "figure"  # figure | table
    context: list[str] = field(default_factory=list)
    local_path: str | None = None


@dataclass
class PaperBundle:
    arxiv_id: str | None
    title: str
    abstract: str
    markdown: str
    tex_sources: list[str]
    figures: list[Figure]
    code_urls: list[str]
    source_dir: Path
    workdir: Path
    source_kind: str  # arxiv_eprint | pdf | text

    def figure_files(self) -> list[Path]:
        out = []
        for fig in self.figures:
            if fig.local_path and Path(fig.local_path).exists():
                out.append(Path(fig.local_path))
        return out


def parse_arxiv_id(text: str) -> str | None:
    """Extract a bare arXiv id like 2510.05096 from a URL or raw id."""
    text = text.strip()
    m = ARXIV_URL_RE.search(text)
    if m:
        return m.group("id")
    m = re.fullmatch(r"(\d{4}\.\d{4,5}(?:v\d+)?)", text)
    if m:
        return m.group(1)
    m = ARXIV_ID_RE.search(text)
    return m.group("id") if m else None


def resolve_input_kind(path_or_url: str) -> str:
    """Return 'arxiv' | 'pdf' | 'dir' | 'text' | 'unknown'."""
    s = path_or_url.strip()
    if parse_arxiv_id(s):
        return "arxiv"
    if re.match(r"https?://", s):
        low = s.lower()
        if low.endswith(".pdf"):
            return "pdf"
        return "unknown"
    p = Path(s)
    if p.is_dir():
        return "dir"
    if p.is_file():
        if p.suffix.lower() == ".pdf":
            return "pdf"
        if p.suffix.lower() in {".tex", ".md", ".txt"}:
            return "text"
    return "unknown"


def ingest(path_or_url: str, workdir: Path, *, timeout: float = 60.0) -> PaperBundle:
    """Ingest a paper into `workdir`. Writes source/ + figures/."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "source").mkdir(exist_ok=True)
    (workdir / "figures").mkdir(exist_ok=True)

    kind = resolve_input_kind(path_or_url)
    if kind == "arxiv":
        return ingest_arxiv(parse_arxiv_id(path_or_url) or path_or_url, workdir, timeout=timeout)
    if kind == "pdf":
        return ingest_pdf(Path(path_or_url), workdir)
    if kind == "dir":
        return ingest_dir(Path(path_or_url), workdir)
    if kind == "text":
        return ingest_text(Path(path_or_url), workdir)
    raise ValueError(f"Unsupported input: {path_or_url!r}")


def ingest_arxiv(arxiv_id: str, workdir: Path, *, timeout: float = 60.0) -> PaperBundle:
    """Download https://arxiv.org/e-print/<id> and unpack LaTeX + original figures."""
    arxiv_id = arxiv_id.split("v")[0] if re.match(r"^\d{4}\.\d{4,5}v\d+$", arxiv_id) else arxiv_id
    src_dir = workdir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)
    (workdir / "figures").mkdir(exist_ok=True)
    tarball = src_dir / f"{arxiv_id}.tar.gz"
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    _download(url, tarball, timeout=timeout)

    extract_root = src_dir / "eprint"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True, exist_ok=True)
    _unpack(tarball, extract_root)

    return _build_from_tex_tree(extract_root, workdir, arxiv_id=arxiv_id, source_kind="arxiv_eprint")


def ingest_pdf(pdf_path: Path, workdir: Path) -> PaperBundle:
    """PDF fallback: extract text + embedded images via PyMuPDF when available."""
    pdf_path = pdf_path.resolve()
    src_dir = workdir / "source"
    dest_pdf = src_dir / pdf_path.name
    if pdf_path != dest_pdf:
        shutil.copy2(pdf_path, dest_pdf)

    text_parts: list[str] = []
    title = pdf_path.stem
    abstract = ""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        md = (workdir / "source" / "paper.md")
        md.write_text(f"# {title}\n\n(PDF text extraction unavailable — install PyMuPDF)\n", encoding="utf-8")
        return PaperBundle(
            arxiv_id=None,
            title=title,
            abstract=abstract,
            markdown=md.read_text(encoding="utf-8"),
            tex_sources=[],
            figures=[],
            code_urls=[],
            source_dir=src_dir,
            workdir=workdir,
            source_kind="pdf",
        )

    doc = fitz.open(dest_pdf)
    fig_dir = workdir / "figures"
    figures: list[Figure] = []
    fig_idx = 0
    for page_i, page in enumerate(doc):
        text_parts.append(page.get_text("text"))
        for img_i, info in enumerate(page.get_images(full=True)):
            xref = info[0]
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:
                continue
            if pix.n - pix.alpha >= 4:  # CMYK
                pix = fitz.Pixmap(fitz.csRGB, pix)
            fig_idx += 1
            name = f"fig_{fig_idx:02d}_p{page_i + 1}.png"
            out = fig_dir / name
            pix.save(str(out))
            figures.append(
                Figure(
                    id=f"Fig{fig_idx}",
                    path=name,
                    caption=f"Embedded image from page {page_i + 1}",
                    label=f"fig:auto{fig_idx}",
                    kind="figure",
                    context=[],
                    local_path=str(out),
                )
            )
    doc.close()

    full_text = "\n\n".join(text_parts)
    title, abstract = _title_abstract_from_text(full_text)
    markdown = _text_to_markdown(full_text, title, abstract)
    md_path = src_dir / "paper.md"
    md_path.write_text(markdown, encoding="utf-8")

    code_urls = list(dict.fromkeys(m.group(0).rstrip(".,;)") for m in GITHUB_RE.finditer(full_text)))

    return PaperBundle(
        arxiv_id=parse_arxiv_id(pdf_path.stem) or parse_arxiv_id(full_text[:2000]),
        title=title,
        abstract=abstract,
        markdown=markdown,
        tex_sources=[],
        figures=figures,
        code_urls=code_urls,
        source_dir=src_dir,
        workdir=workdir,
        source_kind="pdf",
    )


def ingest_dir(src: Path, workdir: Path) -> PaperBundle:
    """Treat a directory as an already-extracted arXiv e-print tree."""
    return _build_from_tex_tree(src, workdir, arxiv_id=None, source_kind="arxiv_eprint", copy=True)


def discover_local_figures(workdir: Path) -> list[Figure]:
    """Pick up images already sitting in workdir/figures (or workdir root)."""
    workdir = Path(workdir)
    fig_dir = workdir / "figures"
    figures: list[Figure] = []
    sources = []
    if fig_dir.is_dir():
        sources.extend(sorted(fig_dir.iterdir()))
    root_imgs = [p for p in list(workdir.glob("*.png")) + list(workdir.glob("*.jpg")) if p.is_file()]
    sources.extend(sorted(root_imgs))
    seen: set[str] = set()
    idx = 0
    for p in sources:
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        key = p.name
        if key in seen:
            continue
        seen.add(key)
        idx += 1
        kind = "table" if "table" in p.name.lower() else "figure"
        prefix = "Table" if kind == "table" else "Fig"
        n_same = sum(1 for f in figures if f.kind == kind) + 1
        figures.append(
            Figure(
                id=f"{prefix}{n_same}",
                path=p.name,
                caption=p.stem.replace("_", " "),
                label="",
                kind=kind,
                context=[],
                local_path=str(p),
            )
        )
    return figures


def ingest_text(text_path: Path, workdir: Path) -> PaperBundle:
    raw = text_path.read_text(encoding="utf-8", errors="replace")
    src_dir = workdir / "source"
    src_dir.mkdir(parents=True, exist_ok=True)
    dest = src_dir / text_path.name
    if text_path.resolve() != dest.resolve():
        dest.write_text(raw, encoding="utf-8")
    else:
        raw = dest.read_text(encoding="utf-8", errors="replace")
    title = text_path.stem
    abstract = ""
    figures: list[Figure] = []
    if text_path.suffix.lower() == ".tex":
        bundle = _parse_tex_corpus({"main.tex": raw}, workdir, arxiv_id=None, source_kind="text", tex_root=src_dir)
        if not bundle.figures:
            bundle.figures = discover_local_figures(workdir)
        return bundle
    # prefer existing paper.md as the markdown body (pipeline contract)
    md_path = src_dir / "paper.md"
    if md_path.exists() and md_path.resolve() != Path(text_path).resolve():
        md = md_path.read_text(encoding="utf-8")
    elif md_path.resolve() == Path(text_path).resolve():
        md = raw
    else:
        md = f"# {title}\n\n{raw}\n"
        md_path.write_text(md, encoding="utf-8")
    title_m = re.search(r"^#\s+(.+)$", md, re.M)
    if title_m:
        title = title_m.group(1).strip()
    figures = discover_local_figures(workdir)
    code_urls = list(dict.fromkeys(m.group(0).rstrip(".,;)") for m in GITHUB_RE.finditer(md)))
    return PaperBundle(
        arxiv_id=parse_arxiv_id(md[:2000]) or parse_arxiv_id(text_path.name),
        title=title,
        abstract=abstract,
        markdown=md,
        tex_sources=[],
        figures=figures,
        code_urls=code_urls,
        source_dir=src_dir,
        workdir=workdir,
        source_kind="text",
    )


def _build_from_tex_tree(
    root: Path,
    workdir: Path,
    *,
    arxiv_id: str | None,
    source_kind: str,
    copy: bool = False,
) -> PaperBundle:
    if copy:
        dest = workdir / "source" / "tree"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root, dest)
        root = dest

    tex_files = sorted(root.rglob("*.tex"))
    if not tex_files:
        # maybe only images + pdf — fall back to any pdf
        pdfs = sorted(root.rglob("*.pdf"))
        if pdfs:
            return ingest_pdf(pdfs[0], workdir)
        raise FileNotFoundError(f"No .tex or .pdf under {root}")

    corpus: dict[str, str] = {}
    for tf in tex_files:
        rel = str(tf.relative_to(root))
        corpus[rel] = tf.read_text(encoding="utf-8", errors="replace")
    corpus = _expand_inputs(corpus, root)
    return _parse_tex_corpus(corpus, workdir, arxiv_id=arxiv_id, source_kind=source_kind, tex_root=root)


def _expand_inputs(corpus: dict[str, str], root: Path) -> dict[str, str]:
    """Inline \\input/\\include bodies when the target file exists (depth-limited)."""
    merged = dict(corpus)
    for _ in range(5):
        changed = False
        for name, body in list(merged.items()):
            def repl(m: re.Match) -> str:
                rel = m.group("path")
                for cand in (rel, rel + ".tex", str((Path(name).parent / rel).as_posix()), str((Path(name).parent / (rel + ".tex")).as_posix())):
                    if cand in merged and cand != name:
                        return "\n" + merged[cand] + "\n"
                    p = root / cand
                    if p.is_file():
                        return "\n" + p.read_text(encoding="utf-8", errors="replace") + "\n"
                return ""

            new_body = INPUT_RE.sub(repl, body)
            if new_body != body:
                merged[name] = new_body
                changed = True
        if not changed:
            break
    return merged


def _parse_tex_corpus(
    corpus: dict[str, str],
    workdir: Path,
    *,
    arxiv_id: str | None,
    source_kind: str,
    tex_root: Path,
) -> PaperBundle:
    # Prefer main.tex / paper.tex / largest
    main_name = None
    for cand in ("main.tex", "paper.tex", "article.tex", "ms.tex", "root.tex"):
        if cand in corpus:
            main_name = cand
            break
    if main_name is None:
        main_name = max(corpus.keys(), key=lambda k: len(corpus[k]))
    combined = "\n\n".join(f"% ===== {k} =====\n{v}" for k, v in corpus.items())

    title = _first_brace(combined, "title") or Path(main_name).stem
    abstract = _first_brace(combined, "abstract") or ""
    abstract = _strip_tex(abstract)

    figures = _extract_figures(combined, tex_root, workdir)
    code_urls = list(dict.fromkeys(m.group(0).rstrip(".,;)") for m in GITHUB_RE.finditer(combined)))
    if not figures:
        figures = discover_local_figures(workdir)
    md = tex_to_markdown(combined, title=title, abstract=abstract, figures=figures)
    (workdir / "source" / "paper.md").write_text(md, encoding="utf-8")
    # copy tex sources listing
    listing = workdir / "source" / "tex_files.txt"
    listing.write_text("\n".join(corpus.keys()) + "\n", encoding="utf-8")

    return PaperBundle(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        markdown=md,
        tex_sources=list(corpus.keys()),
        figures=figures,
        code_urls=code_urls,
        source_dir=workdir / "source",
        workdir=workdir,
        source_kind=source_kind,
    )


def _extract_figures(tex: str, tex_root: Path, workdir: Path) -> list[Figure]:
    figures: list[Figure] = []
    fig_dir = workdir / "figures"
    fig_dir.mkdir(exist_ok=True)
    idx = 0

    # Figure environments with includegraphics + caption + label
    for kind, env_re in (("figure", BEGIN_FIG_RE), ("table", BEGIN_TBL_RE)):
        for m in env_re.finditer(tex):
            body = m.group("body")
            idx += 1
            paths = INCLUDEGRAPHICS_RE.findall(body)
            cap_m = CAPTION_RE.search(body)
            caption = _strip_tex(cap_m.group("cap")) if cap_m else ""
            label = ""
            lm = LABEL_RE.search(body)
            if lm:
                label = lm.group("label")
            elif cap_m and cap_m.group("label"):
                label = cap_m.group("label")
            src_rel = paths[0].strip() if paths else None
            local = _copy_graphic(src_rel, tex_root, fig_dir, idx, kind) if src_rel else None
            figures.append(
                Figure(
                    id=f"{kind.capitalize() if kind == 'table' else 'Fig'}{sum(1 for f in figures if f.kind == kind) + 1}",
                    path=src_rel,
                    caption=caption,
                    label=label,
                    kind=kind,
                    context=[],
                    local_path=str(local) if local else None,
                )
            )

    # Standalone includegraphics not captured above
    seen_paths = {f.path for f in figures if f.path}
    for m in INCLUDEGRAPHICS_RE.finditer(tex):
        rel = m.group("path").strip()
        if rel in seen_paths:
            continue
        idx += 1
        local = _copy_graphic(rel, tex_root, fig_dir, idx, "figure")
        figures.append(
            Figure(
                id=f"Fig{sum(1 for f in figures if f.kind == 'figure') + 1}",
                path=rel,
                caption="",
                label="",
                kind="figure",
                context=[],
                local_path=str(local) if local else None,
            )
        )

    # Attach contexts: paragraphs that \ref the label
    label_to_paras = _paragraphs_by_ref(tex)
    for fig in figures:
        keys = [fig.label] if fig.label else []
        if fig.path:
            keys.append(Path(fig.path).stem)
        ctx: list[str] = []
        for key in keys:
            if not key:
                continue
            ctx.extend(label_to_paras.get(key, []))
        # de-dup preserve order
        seen = set()
        for c in ctx:
            c2 = c.strip()
            if c2 and c2 not in seen:
                seen.add(c2)
                fig.context.append(c2)
    return figures


def _copy_graphic(rel: str | None, tex_root: Path, fig_dir: Path, idx: int, kind: str) -> Path | None:
    if not rel:
        return None
    rel = rel.strip()
    candidates = []
    base = Path(rel)
    stem = base.with_suffix("")
    for ext in ("", ".png", ".pdf", ".jpg", ".jpeg", ".eps", ".svg"):
        candidates.append(tex_root / f"{stem}{ext}" if ext else tex_root / rel)
        candidates.append(tex_root / base)
    # also search recursively by basename
    name = Path(rel).name
    for p in candidates:
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS | {""}:
            dest = fig_dir / f"{kind}_{idx:02d}{p.suffix or '.png'}"
            shutil.copy2(p, dest)
            return dest
    matches = list(tex_root.rglob(name + ".*")) + list(tex_root.rglob(name))
    for p in matches:
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            dest = fig_dir / f"{kind}_{idx:02d}{p.suffix}"
            shutil.copy2(p, dest)
            return dest
    # try stem match
    for p in tex_root.rglob(stem.name + ".*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            dest = fig_dir / f"{kind}_{idx:02d}{p.suffix}"
            shutil.copy2(p, dest)
            return dest
    return None


def _paragraphs_by_ref(tex: str) -> dict[str, list[str]]:
    paras = re.split(r"\n\s*\n", tex)
    out: dict[str, list[str]] = {}
    for para in paras:
        text = _strip_tex(para)
        if len(text) < 40:
            continue
        labels = REF_RE.findall(para)
        for lab in labels:
            out.setdefault(lab, []).append(text[:800])
    return out


def _first_brace(tex: str, cmd: str) -> str | None:
    m = re.search(rf"\\{cmd}\{{", tex)
    if not m:
        return None
    i = m.end()
    depth = 1
    buf = []
    while i < len(tex) and depth:
        ch = tex[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        buf.append(ch)
        i += 1
    return "".join(buf)


def _strip_tex(s: str) -> str:
    s = re.sub(r"%.*", "", s)
    s = re.sub(r"\\(?:textbf|textit|emph|textrm|texttt|underline|mathrm|mathbf|bm|url|href|cite[pt]?)\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:ref|autoref|label|cite[pt]?|eqref|cref|Cref)\{[^{}]*\}", "", s)
    s = re.sub(r"\\(?:section|subsection|subsubsection|chapter|paragraph)\*?\{([^{}]*)\}", r"\n## \1\n", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}$~]", " ", s)
    s = re.sub(r"\\", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def tex_to_markdown(tex: str, *, title: str, abstract: str, figures: list[Figure] | None = None) -> str:
    lines = [f"# {_strip_tex(title)}", ""]
    if abstract:
        lines += ["## Abstract", "", _strip_tex(abstract), ""]

    # body sections
    body = tex
    # drop preamble-ish macros roughly
    body = re.sub(r"\\documentclass\{[^}]*\}", "", body)
    body = re.sub(r"\\begin\{abstract\}.*?\\end\{abstract\}", "", body, flags=re.S)
    body = re.sub(r"\\title\{.*?\}", "", body, flags=re.S)

    chunks = SECTION_RE.split(body)
    # SECTION_RE with one group → [preamble, title, sec, title, sec, ...]
    if len(chunks) >= 3:
        pre = _strip_tex(chunks[0])
        if pre:
            lines += [pre, ""]
        for i in range(1, len(chunks), 2):
            sec_title = _strip_tex(chunks[i])
            sec_body = _strip_tex(chunks[i + 1]) if i + 1 < len(chunks) else ""
            lines += [f"## {sec_title}", "", sec_body, ""]
    else:
        lines += [_strip_tex(body), ""]

    if figures:
        lines += ["## Figures & Tables", ""]
        for fig in figures:
            cap = fig.caption or "(no caption)"
            lines.append(f"- **{fig.id}** `{fig.path or fig.label or ''}` — {cap}")
            for c in fig.context[:2]:
                lines.append(f"  - context: {c[:240]}")
        lines.append("")

    md = "\n".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def _title_abstract_from_text(text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = lines[0][:200] if lines else "Untitled"
    abstract = ""
    joined = text.lower()
    if "abstract" in joined:
        i = joined.index("abstract")
        tail = text[i + 8 : i + 2500]
        tail = re.sub(r"^[\s:.—-]+", "", tail)
        stop = re.search(r"\n\s*(?:1\.?\s+)?(?:introduction|keywords|index terms)", tail, re.I)
        abstract = tail[: stop.start()].strip() if stop else tail[:1500].strip()
    return title, abstract


def _text_to_markdown(text: str, title: str, abstract: str) -> str:
    return f"# {title}\n\n## Abstract\n\n{abstract}\n\n## Body\n\n{text}\n"


def _download(url: str, dest: Path, *, timeout: float = 60.0) -> None:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "paper2youtube/0.1 (research pipeline)"})
    with urlopen(req, timeout=timeout) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def _unpack(archive: Path, dest: Path) -> None:
    """Unpack tar.gz / tar / zip e-print bundles."""
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    # tarfile handles .tar.gz / .tar.bz2 / .tar
    try:
        with tarfile.open(archive, "r:*") as tf:
            _safe_extract(tf, dest)
    except tarfile.ReadError:
        # some e-prints are a single .tex or .pdf without archive wrapper
        raw = archive.read_bytes()
        if raw[:4] == b"%PDF":
            (dest / "paper.pdf").write_bytes(raw)
        else:
            (dest / "main.tex").write_bytes(raw)


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"Blocked path traversal in tar: {member.name}")
        tf.extract(member, dest)  # noqa: S202 — validated above


def detect_code_urls(markdown: str) -> list[str]:
    urls = [m.group(0).rstrip(".,;)") for m in GITHUB_RE.finditer(markdown)]
    urls += [f"https://huggingface.co/papers/{m.group('id')}" for m in HF_PAPER_RE.finditer(markdown)]
    return list(dict.fromkeys(urls))