"""Stage 3 — structured slide plan + per-figure spoken script (map-reduce)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .code_checker import CodeStatus, code_status_to_md
from .ingest import Figure, PaperBundle
from .llm import LLMClient, extract_json

FIXED_SECTIONS = [
    ("overview_code", "Overview & Code"),
    ("motivation", "Motivation"),
    ("methodology", "Methodology"),
    ("experimental_setup", "Experimental Setup"),
    ("main_results", "Main Results & Ablation"),
]

FIGURE_PROMPT_ZH = (
    "聽眾正在跑步，無法隨時盯著螢幕。請先用一句話建立這張圖的空間結構"
    "（例如：這是一張由左至右的三階段 Pipeline 圖，或是橫軸為訓練步數、縱軸為準確率的折線圖），"
    "接著依序拆解圖中每個區塊的資料流向與關鍵數值對比，讓聽眾光靠聽覺就能在腦中還原畫面。"
    "口語化、避免公式朗讀、中英專有名詞可夾雜。輸出繁體中文口語講稿，約 120–220 字。"
)

SECTION_PROMPT_ZH = (
    "你是技術論文的口語講者。聽眾在跑步，只靠聽覺理解。"
    "請針對章節「{section}」寫一段繁體中文口語講稿（約 100–180 字），"
    "聚焦可驗證的技術細節：痛點、輸入輸出、模組、loss/訓練、資料集、指標、勝負點。"
    "不要空泛形容詞。可夾雜英文專有名詞。"
)


@dataclass
class SlideItem:
    id: str
    kind: str  # fixed | figure | table
    title: str
    slide_markdown: str
    narration: str
    figure_path: str | None = None
    figure_id: str | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SlidesPlan:
    paper_title: str
    arxiv_id: str | None
    lang: str
    code_intro: str
    items: list[SlideItem] = field(default_factory=list)
    notes_markdown: str = ""

    def to_dict(self) -> dict:
        return {
            "paper_title": self.paper_title,
            "arxiv_id": self.arxiv_id,
            "lang": self.lang,
            "code_intro": self.code_intro,
            "items": [i.to_dict() for i in self.items],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str | Path) -> "SlidesPlan":
        if isinstance(raw, Path):
            data = json.loads(raw.read_text(encoding="utf-8"))
        else:
            data = extract_json(raw) if isinstance(raw, str) and not raw.strip().startswith("{") else json.loads(raw)
        items = [SlideItem(**it) for it in data.get("items", [])]
        return cls(
            paper_title=data.get("paper_title", ""),
            arxiv_id=data.get("arxiv_id"),
            lang=data.get("lang", "zh-TW"),
            code_intro=data.get("code_intro", ""),
            items=items,
            notes_markdown=data.get("notes_markdown", ""),
        )


def build_plan(
    bundle: PaperBundle,
    code_status: CodeStatus,
    llm: LLMClient,
    *,
    lang: str = "zh-TW",
    max_figures: int = 40,
) -> SlidesPlan:
    """5 fixed section nodes + one node per figure/table (forced loop)."""
    plan = SlidesPlan(
        paper_title=bundle.title,
        arxiv_id=bundle.arxiv_id,
        lang=lang,
        code_intro=code_status.spoken_intro,
    )

    md = bundle.markdown
    code_md = code_status_to_md(code_status)

    for key, title in FIXED_SECTIONS:
        section_text = _section_slice(md, title)
        narration = _llm_section(llm, title, section_text, code_md if key == "overview_code" else "")
        slide_md = _fixed_slide_md(title, section_text, code_status if key == "overview_code" else None)
        plan.items.append(
            SlideItem(
                id=key,
                kind="fixed",
                title=title,
                slide_markdown=slide_md,
                narration=narration,
                sources=["overview_code" if key == "overview_code" else title],
            )
        )

    # FORCED per-figure loop — never skip figures just because LLM got lazy.
    figures = bundle.figures[:max_figures]
    for i, fig in enumerate(figures, start=1):
        item = _figure_item(llm, fig, index=i, bundle=bundle)
        plan.items.append(item)

    plan.notes_markdown = render_notes(plan, bundle, code_status)
    return plan


def _figure_item(llm: LLMClient, fig: Figure, *, index: int, bundle: PaperBundle) -> SlideItem:
    label = fig.id if fig.kind == "table" else f"Fig.{index}"
    title = f"{label}: {fig.caption[:80] if fig.caption else fig.kind}"
    ctx = "\n".join(fig.context[:3])
    prompt = (
        f"{FIGURE_PROMPT_ZH}\n\n"
        f"【圖說】{fig.caption or '(none)'}\n"
        f"【內文引用】\n{ctx or '(none)'}\n"
        f"【類型】{fig.kind}\n"
        "輸出 JSON: {\"narration\": \"...\", \"spatial\": \"一句空間結構\", \"bullets\": [\"...\", \"...\"]}"
    )
    images = [fig.local_path] if fig.local_path and Path(fig.local_path).exists() else None
    vlm_model = getattr(getattr(llm, "settings", None), "vlm_model", None)
    raw = llm.chat(prompt, system="你是圖表口語解析專家。只輸出 JSON。", images=images, model=vlm_model)
    data = _safe_json(raw)
    narration = str(data.get("narration") or "").strip()
    if not narration:
        narration = _fallback_figure_narration(fig)
    bullets = data.get("bullets") or []
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    slide_md = _figure_slide_md(title, fig, bullets, spatial=str(data.get("spatial") or ""))
    return SlideItem(
        id=f"figure_{index:02d}" if fig.kind != "table" else f"table_{index:02d}",
        kind=fig.kind,
        title=title,
        slide_markdown=slide_md,
        narration=narration,
        figure_path=fig.local_path,
        figure_id=fig.id,
        sources=fig.context[:2],
    )


def _fixed_slide_md(title: str, section_text: str, code_status: CodeStatus | None) -> str:
    lines = [f"# {title}", ""]
    if code_status:
        lines += [f"> {code_status.spoken_intro}", ""]
    bullets = _bullets_from_text(section_text, limit=6)
    for b in bullets:
        lines.append(f"- {b}")
    return "\n".join(lines) + "\n"


def _figure_slide_md(title: str, fig: Figure, bullets: list[Any], spatial: str) -> str:
    lines = [f"# {title}", ""]
    if spatial:
        lines += [f"**空間結構：** {spatial}", ""]
    if fig.local_path:
        # relative name only — slides renderer resolves under figures/
        name = Path(fig.local_path).name
        lines += [f"![figure](figures/{name})", ""]
    for b in bullets:
        lines.append(f"- {b}")
    if fig.caption:
        lines += ["", f"*{fig.caption[:200]}*"]
    return "\n".join(lines) + "\n"


def _llm_section(llm: LLMClient, title: str, section_text: str, extra: str = "") -> str:
    prompt = SECTION_PROMPT_ZH.format(section=title)
    prompt += f"\n\n【章節內容摘錄】\n{section_text[:4000]}\n"
    if extra:
        prompt += f"\n【程式碼狀態】\n{extra[:1500]}\n"
    prompt += "\n只輸出講稿正文，不要 Markdown 標題。"
    text = llm.chat(prompt, system="你是技術論文口語講者。")
    return _clean_narration(text)


def _fallback_figure_narration(fig: Figure) -> str:
    cap = fig.caption or "這張圖"
    return (
        f"接著看{fig.id}。{cap}。"
        "請把畫面想成由主要模組與箭頭組成的示意圖；"
        "重點在資料如何從輸入流向輸出，以及圖中標示的關鍵數值對比。"
    )


def _safe_json(raw: str) -> dict:
    try:
        data = extract_json(raw)
        return data if isinstance(data, dict) else {"narration": str(data)}
    except Exception:
        return {"narration": raw.strip()}


def _clean_narration(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^#+\s.*$", "", text, flags=re.M).strip()
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text.strip()


def _bullets_from_text(text: str, limit: int = 6) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["（本節摘要待補）"]
    # prefer existing bullets / sentences
    parts = re.split(r"\n|(?<=[。！？.!?])\s+", text)
    bullets = []
    for p in parts:
        p = p.strip("-•* \t")
        if len(p) < 8:
            continue
        bullets.append(p[:180])
        if len(bullets) >= limit:
            break
    return bullets or [text[:180]]


def _section_slice(md: str, title: str) -> str:
    """Grab a markdown H2 section by fuzzy title."""
    pattern = re.compile(rf"^##\s+.*{re.escape(title)}.*$", re.I | re.M)
    m = pattern.search(md)
    if not m:
        # keyword fallback
        keys = {
            "Overview & Code": r"overview|abstract|contribution|code",
            "Motivation": r"motivation|background|related|introduction",
            "Methodology": r"method|approach|architecture|model|framework|algorithm",
            "Experimental Setup": r"experiment.*setup|setup|dataset|implementation|evaluation",
            "Main Results & Ablation": r"result|ablation|discussion|analysis",
        }
        pat = keys.get(title, re.escape(title))
        m = re.search(rf"^##\s+.*({pat}).*$", md, re.I | re.M)
    if not m:
        return md[:2500]
    start = m.end()
    nxt = re.search(r"^##\s+", md[start:], re.M)
    end = start + nxt.start() if nxt else min(len(md), start + 4000)
    return md[start:end].strip()[:4000]


def render_notes(plan: SlidesPlan, bundle: PaperBundle, code_status: CodeStatus) -> str:
    """Full technical notes markdown (output_notes.md)."""
    lines = [
        f"# {bundle.title}",
        "",
        f"- **arXiv:** {bundle.arxiv_id or 'n/a'}",
        f"- **source kind:** {bundle.source_kind}",
        f"- **figures:** {len(bundle.figures)}",
        "",
        "## Code availability",
        "",
        code_status.spoken_intro,
        "",
        code_status_to_md(code_status),
        "## Abstract",
        "",
        (bundle.abstract or "(none)")[:3000],
        "",
        "## Narrated outline",
        "",
    ]
    for i, item in enumerate(plan.items, 1):
        lines += [f"### {i:02d}. {item.title}", "", item.narration, ""]
        if item.sources:
            lines.append("Sources: " + "; ".join(s[:120] for s in item.sources[:2]))
            lines.append("")
    lines += ["## Full markdown digest", "", bundle.markdown[:12000], ""]
    return "\n".join(lines)


def render_notes_from_plan(plan: SlidesPlan) -> str:
    """Plan-only notes fallback — no synthetic PaperBundle/CodeStatus."""
    lines = [
        f"# {plan.paper_title or 'Untitled'}",
        "",
    ]
    if plan.arxiv_id:
        lines += [f"- **arXiv:** {plan.arxiv_id}", ""]
    if plan.code_intro:
        lines += ["## Code availability", "", plan.code_intro, ""]
    lines += ["## Narrated outline", ""]
    for i, item in enumerate(plan.items, 1):
        lines += [f"### {i:02d}. {item.title}", "", item.narration, ""]
        if item.figure_path:
            lines += [f"Figure: `{item.figure_path}`", ""]
        if item.sources:
            lines.append("Sources: " + "; ".join(s[:120] for s in item.sources[:2]))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_plan(plan: SlidesPlan, workdir: Path) -> tuple[Path, Path]:
    """Persist slides_plan.json + output_notes.md.

    Prefers `plan.notes_markdown` (filled by `build_plan`); otherwise writes a
    plan-only outline. Callers that need full digest notes should run
    `build_plan` / `render_notes` first so `notes_markdown` is populated.
    """
    workdir = Path(workdir)
    plan_path = workdir / "slides_plan.json"
    notes_path = workdir / "output_notes.md"
    plan_path.write_text(plan.to_json(), encoding="utf-8")
    notes = plan.notes_markdown or render_notes_from_plan(plan)
    notes_path.write_text(notes, encoding="utf-8")
    return plan_path, notes_path