---
name: paper2youtube
description: "Turn an arXiv link/ID or PDF into a deep technical notes file, slide deck, per-figure spoken audio, and YouTube-ready video with chapters. Use when the user asks to paper2youtube / 把論文變成 YouTube 影片 / 產生論文口語解析音檔 / 產出投影片+語音+影片 / 開跑步聽的論文講解影片, or points at an arXiv URL/ID or .pdf and wants slides+audio+video. Also use when asked to run or resume the paper2youtube pipeline on a literature/ folder. Not for plain paper summarization without media, PDF translation, or generic PPTX decks without narration."
---

# paper2youtube — arXiv/PDF → notes + slides + per-figure audio + YouTube video

## What this does

Modular pipeline aligned on **one slide/node per segment**, so each PNG is bound to exactly one narration audio (zero timeline drift):

1. `ingest` — arXiv e-print source first (original figures + `\label`/`\ref` context), PDF fallback
2. `code_check` — GitHub + Hugging Face Papers API → real code vs empty-shell
3. `script` — 5 fixed sections + **forced per-figure VLM loop** → `slides_plan.json` + `output_notes.md`
4. `slides` — `output_slides.pptx` + `slides/slide_NN.png`
5. `tts` — `audio/audio_NN.wav` + `output_full_audio.mp3|.wav`
6. `video` — `output_video.mp4` + `chapters.txt` (YouTube chapters) + optional upload

## Canonical paths

Resolve the repo root as `PAPER2YOUTUBE_ROOT` (env) or `$HOME/ai_for_research/paper2youtube`.

| Item | Path |
|------|------|
| Code root | `$PAPER2YOUTUBE_ROOT` |
| CLI | `$PAPER2YOUTUBE_ROOT/run.py` |
| Package | `$PAPER2YOUTUBE_ROOT/paper2youtube/` |
| Tests | `$PAPER2YOUTUBE_ROOT/tests/` |
| Default workdir | the `--input` dir if it is a directory, else `./literature/` |

Python: use the active env that has `openai`, `pptx`, `PIL`, `fitz`, `pytest` (wrapper default: `python3` on PATH or `PAPER2YOUTUBE_PYTHON`).

## When to use

- 「paper2youtube」「把這篇論文做成 YouTube 影片」「產出投影片+口語音檔」
- 「輸入 arXiv 連結或 PDF → 技術文字稿、投影片、全圖表口語解析音檔、影片」
- Resume / continue an existing `literature/` folder (checkpoint via `manifest.json`)

**Not for:** 純文字摘要、PDF 翻譯、只要 PPTX 不要語音、與論文無關的一般簡報。

## Instructions

### Step 1 — Resolve input and workdir

Accepted `--input` forms:

- arXiv URL or ID: `https://arxiv.org/abs/2510.05096`, `2510.05096`
- PDF path: `/path/paper.pdf`
- e-print tree dir (contains `.tex` + figures)
- literature output dir: `literature/` (used for resume or PDF dropped inside)

Workdir rules:

- `python run.py --input literature/` → outputs stay in `literature/`
- `python run.py --input https://arxiv.org/abs/...` → default `./literature/` unless `--workdir` is set

### Step 2 — Run the pipeline

```bash
cd "$PAPER2YOUTUBE_ROOT"   # or $HOME/ai_for_research/paper2youtube
python3 run.py --input literature/
```

Live LLM/TTS (default):

```bash
python3 run.py \
  --input https://arxiv.org/abs/2510.05096 \
  --workdir literature/2510.05096
```

Offline / CI smoke (no LLM, silent audio):

```bash
python3 run.py --input literature/ --offline
```

Partial stages / resume from a stage:

```bash
python3 run.py --input literature/ --stages slides,tts,video
python3 run.py --input literature/ --from-stage tts
```

Force re-run (ignore checkpoints):

```bash
python3 run.py --input literature/ --no-resume
```

Optional YouTube upload (needs `youtubeuploader` on PATH):

```bash
python3 run.py --input literature/ --upload
```

### Step 3 — Verify deliverables in the literature dir

| File | Meaning |
|------|---------|
| `source/` `figures/` | extracted text + original figures |
| `output_notes.md` | deep technical notes + full narrated outline |
| `slides_plan.json` | one node per slide (markdown + narration + figure path) |
| `output_slides.pptx` | slide deck |
| `slides/slide_NN.png` | per-slide images used by the video |
| `audio/audio_NN.wav` | per-slide narration |
| `output_full_audio.mp3` or `.wav` | concatenated full audio |
| `chapters.txt` | YouTube chapters (`00:00 Title`) |
| `youtube_description.txt` | title + arXiv link + chapters + code note |
| `output_video.mp4` | final video (needs `ffmpeg`) |
| `manifest.json` | checkpoint / stage status |

### Step 4 — Report to the user

Print the workdir, stage summary from `manifest.json`, and any `ffmpeg`/TTS warnings. Do not claim `output_video.mp4` exists unless the file is present.

## Important

- **Per-figure loop is mandatory.** Never summarize “all figures in one prompt”. The builder always emits one slide node per figure/table.
- **Slide ↔ audio alignment** is 1:1 (each `slide_NN.png` is shown for exactly `audio_NN.wav` duration). Do not build one long audio and force-align.
- **arXiv first:** prefer `https://arxiv.org/e-print/<id>` source over PDF rendering so figures are lossless and `\ref` contexts are recoverable.
- **Code honesty:** if GitHub is an empty shell / “code upon acceptance”, say so in the opening narration (`code_status.json` / `spoken_intro`).
- **Graceful degradation:** missing `edge-tts` → silent WAV + note; missing `ffmpeg` → still write PNG/WAV/chapters and WARN, never pretend the mp4 exists.
- **LLM env:** `PAPER2YOUTUBE_API_KEY` (or `OPENAI_API_KEY` / `MIMO_API_KEY`), `PAPER2YOUTUBE_BASE_URL` (OpenAI-compatible endpoint; do not commit private IPs), `PAPER2YOUTUBE_MODEL`. Private cluster endpoints stay in env / local config only.
- **Tests:** every module has `tests/test_*.py`. After code changes run:

```bash
cd "$PAPER2YOUTUBE_ROOT"
python3 -m pytest tests -q
```

## Output contract (slides_plan.json item)

```json
{
  "id": "figure_01",
  "kind": "figure",
  "title": "Fig.1: ...",
  "slide_markdown": "# Fig.1\n![figure](figures/fig_01.png)\n- ...",
  "narration": "聽覺友善口語講稿…",
  "figure_path": ".../figures/fig_01.png",
  "figure_id": "Fig1",
  "sources": ["context paragraph…"]
}
```

Fixed node ids: `overview_code`, `motivation`, `methodology`, `experimental_setup`, `main_results`. Dynamic: `figure_NN` / `table_NN`.

## Troubleshooting

| Error | Cause | Fix |
|------|-------|-----|
| `No API key` | missing LLM creds | export `MIMO_API_KEY` or `PAPER2YOUTUBE_API_KEY`, or `--offline` |
| `ffmpeg not found` | video/mp3 stage | install ffmpeg; PNG/WAV/chapters still valid |
| `openai package required` | env incomplete | use miniforge python above or `pip install openai` |
| figures empty | PDF without extractable images / tex figure paths | re-run with arXiv id to pull e-print; check `source/eprint` |
| edge-tts missing | optional dep | `pip install edge-tts` or accept silent fallback |
| upload returns nothing | no `youtubeuploader` | install porjo/youtubeuploader + OAuth; or upload `output_video.mp4` manually with `youtube_description.txt` |

## Examples

1. User: 「幫我把 https://arxiv.org/abs/2510.05096 做成跑步可以聽的 YouTube 影片」  
   → `python3 run.py --input https://arxiv.org/abs/2510.05096 --workdir literature/2510.05096`  
   → report notes/slides/audio/video + chapters in that dir.

2. User: 「resume literature/，影片還沒出來」  
   → `python3 run.py --input literature/ --from-stage video`  
   → confirm ffmpeg; if missing, report PNG/WAV/chapters as delivered and explain.

3. User: 「先產出文字稿和投影片就好」  
   → `python3 run.py --input literature/ --stages ingest,code_check,script,slides`