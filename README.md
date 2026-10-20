# paper2youtube

把 **arXiv 連結 / PDF** 一鍵轉成：

1. 深度技術文字稿（`output_notes.md`）
2. 投影片（`output_slides.pptx` + `slides/slide_NN.png`）
3. 全圖表口語解析音檔（`audio/audio_NN.wav` + `output_full_audio.mp3`）
4. YouTube 影片與章節（`output_video.mp4` + `chapters.txt`）

對齊單位是 **投影片頁數（Slide/Node）**：每一頁 PNG 綁定一段講稿音檔，避免長音檔對軸漂移。  
跑步時可用耳機 / 手錶按「下一段」跳到下一張圖表；YouTube Description 自動產生章節時間戳。

---

## 快速開始

```bash
# 安裝為 Python package（建議 editable）
pip install -e ".[dev]"

# 影片 / mp3 需要系統 ffmpeg（conda-forge 亦可）
#   conda install -c conda-forge ffmpeg

# 三種等價呼叫方式
paper2youtube --input literature/
python -m paper2youtube --input literature/
python3 run.py --input literature/          # 相容舊指令

# 或直接餵 arXiv / PDF
paper2youtube --input https://arxiv.org/abs/2510.05096 --workdir literature/2510.05096
paper2youtube --input paper.pdf --workdir literature/mypaper
```

`--input` 若本身就是資料夾，產物會寫在該資料夾（預設 `--input literature/` → 輸出到 `literature/`）。

### 常用選項

| 選項 | 說明 |
|------|------|
| `--stages ingest,code_check,script,slides,tts,video` | 只跑指定階段 |
| `--from-stage tts` | 從某階段跑到結束 |
| `--no-resume` | 忽略 `manifest.json` 斷點 |
| `--offline` | 不打 LLM / TTS 網路（靜音 WAV、規則式腳本） |
| `--upload` | 若有 `youtubeuploader` 則上傳 unlisted |
| `--voice` / `--tts-backend edge\|silent` | TTS 聲線與後端 |
| `--model` / `--base-url` | 覆寫 OpenAI-compatible 端點 |

### Agent Skill

同工作流也包成 skill：`.mimocode/skills/paper2youtube/`  
可由 agent 以 `/paper2youtube` 或「把論文做成 YouTube 影片」觸發。

---

## 輸出契約（寫入 literature/）

| 檔案 | 說明 |
|------|------|
| `source/` | 解析後內文（arXiv e-print 優先） |
| `figures/` | 原始圖表（盡量零失真） |
| `output_notes.md` | 深度技術文字稿 + 逐段講稿大綱 |
| `slides_plan.json` | 每頁投影片 Markdown + 口語講稿 + 圖路徑 |
| `output_slides.pptx` | 投影片檔 |
| `slides/slide_NN.png` | 單頁圖（影片素材） |
| `audio/audio_NN.wav` | 單頁講稿語音 |
| `output_full_audio.mp3` | 完整語音（無 ffmpeg 時為 `.wav`） |
| `output_video.mp4` | 影片（需 ffmpeg） |
| `chapters.txt` | YouTube 章節 `00:00 Title` |
| `youtube_description.txt` | 標題 + arXiv + 章節 + 程式碼狀態 |
| `code_status.json` / `.md` | 開源 Repo 真偽判定 |
| `manifest.json` | 各階段 checkpoint |
| `video_timeline.json` | 頁面 / 音檔 / 章節時間軸 |

### slides_plan.json 單元

固定 5 節點：

1. `overview_code` — 貢獻與程式碼狀態（開頭 1 分鐘報告「真開源 / 空殼 Repo」）
2. `motivation`
3. `methodology`
4. `experimental_setup`
5. `main_results`

之後 **For-Loop 每一張 Figure / Table**（絕不併成單一 prompt），使用聽覺友善腳本：先建立空間結構一句話，再拆解區塊與數值對比。

---

## 架構（模組）

```
input (arXiv / PDF / dir)
  │
  ├─ ingest.py        arXiv e-print（原始圖 + \label/\ref）或 PDF fallback
  ├─ code_checker.py  Regex + GitHub Trees / HF Papers API
  ├─ script_builder.py  5 固定節點 + 逐圖 VLM → slides_plan.json + output_notes.md
  ├─ slides.py        python-pptx + PIL → pptx + slide_NN.png
  ├─ tts.py           edge-tts（可 silent）→ audio_NN.wav + output_full_audio.mp3
  └─ video.py         FFmpeg 逐頁綁定時長 → mp4 + chapters
        run.py / pipeline.py  串接 + manifest 斷點續跑
```

每個模組都有對應測試：`tests/test_*.py`。

```bash
python3 -m pytest tests -q
```

---

## 設定（環境變數）

**請勿把 API key、內網 IP 寫進 repo。** 一律用環境變數：

| 變數 | 說明 | 預設 |
|------|------|------|
| `PAPER2YOUTUBE_API_KEY`（或 `OPENAI_API_KEY` / `MIMO_API_KEY`） | LLM API key | （無） |
| `PAPER2YOUTUBE_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `PAPER2YOUTUBE_MODEL` | 主模型 | `gpt-4o-mini` |
| `PAPER2YOUTUBE_VLM_MODEL` | 圖片解析模型 | 同主模型 |
| `PAPER2YOUTUBE_GITHUB_TOKEN` / `GITHUB_TOKEN` | GitHub API（可選，提高 rate limit） | （無） |
| `PAPER2YOUTUBE_TTS_VOICE` | edge-tts 聲線 | `zh-TW-HsiaoChenNeural` |
| `PAPER2YOUTUBE_TTS_BACKEND` | `edge` \| `silent` | `edge` |
| `PAPER2YOUTUBE_LANG` | 講稿語言 | `zh-TW` |
| `PAPER2YOUTUBE_MAX_FIGURES` | 逐圖節點上限 | `40` |
| `PAPER2YOUTUBE_ROOT` / `PAPER2YOUTUBE_PYTHON` | skill wrapper 路徑 | `$HOME/ai_for_research/paper2youtube` / `python3` |

私有 LLM 叢集請設 `PAPER2YOUTUBE_BASE_URL` + `PAPER2YOUTUBE_MODEL`，**不要**把內網位址 commit 進 `config.py`。

---

## 安裝為 Python package

```bash
pip install -e ".[dev]"     # 開發（含 pytest + edge-tts）
pip install -e ".[tts]"     # 執行期 + edge-tts
pip install .               # 一般安裝
```

安裝後提供 console script：

```text
paper2youtube = paper2youtube.cli:main
```

亦可 `python -m paper2youtube`。API：

```python
from paper2youtube import run_pipeline, load_settings
result = run_pipeline("literature/", "literature/", load_settings(), offline=True)
```

### 依賴

- Python 3.10+（見 `pyproject.toml`）
- 系統 `ffmpeg` / `ffprobe`（輸出 mp3 與 mp4）
- 可選 extras：`tts` / `dev` / `all`
- 可選：`youtubeuploader`（porjo）做 unlisted 上傳

缺依賴時會誠實降級（例如無 ffmpeg → 仍產 PNG/WAV/chapters，並在 manifest 標示 video 未完成）。

---

## 安全 / 機敏資訊

本 repo **刻意不含**：

- API key、token、密碼、私鑰
- 內網 IP / 內部 hostname
- 使用者帳號目錄以外的憑證檔

掃描建議（發佈前）：

```bash
grep -RInE 'api[_-]?key\s*=\s*["\']|password|BEGIN (RSA|OPENSSH|PRIVATE)|10\.[0-9]+\.[0-9]+\.[0-9]+' \
  --include='*.py' --include='*.md' --include='*.sh' .
```

金鑰只放在 shell env 或本機未納管的 `~/.env`。

---

## 測試

```bash
python3 -m pytest tests -q
```

- 各模組皆有 `tests/test_*.py`
- 測試不依賴外網（GitHub 檢查以 monkeypatch / `inspect=False`）
- `--offline` 可做無金鑰 smoke

---

## License

MIT（與 `pyproject.toml` 一致）。第三方工具（edge-tts、ffmpeg、YouTube uploader）請遵守各專案授權。