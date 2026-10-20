# slides_plan.json schema (stable contract)

Each `items[]` element is **one slide + one narration clip**.

| field | type | notes |
|------|------|-------|
| `id` | string | `overview_code` \| `motivation` \| `methodology` \| `experimental_setup` \| `main_results` \| `figure_NN` \| `table_NN` |
| `kind` | string | `fixed` \| `figure` \| `table` |
| `title` | string | slide title / YouTube chapter title |
| `slide_markdown` | string | `#` title + bullets + optional `![figure](figures/…)` |
| `narration` | string | spoken script (zh-TW oral, EN terms allowed) |
| `figure_path` | string\|null | absolute or workdir-relative image |
| `figure_id` | string\|null | paper label like `Fig1` |
| `sources` | string[] | supporting context snippets |

Top-level: `paper_title`, `arxiv_id`, `lang`, `code_intro`, `items[]`, optional `notes_markdown`.

## Hearing-friendly figure prompt (embedded in script_builder)

> 聽眾正在跑步，無法隨時盯著螢幕。請先用一句話建立這張圖的空間結構…接著依序拆解圖中每個區塊的資料流向與關鍵數值對比…

## Fixed section order

1. Overview & Code  
2. Motivation  
3. Methodology  
4. Experimental Setup  
5. Main Results & Ablation  

Then **for-loop every figure/table** (never batched into one prompt).