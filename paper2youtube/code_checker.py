"""Stage 2 — detect real open-source repos vs empty-shell placeholders."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

GITHUB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?=[/\s?#,;:!)\"'\]]|$)",
    re.I,
)
HF_PAPER_API = "https://huggingface.co/api/papers/{arxiv_id}"
GITHUB_TREE_API = "https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
GITHUB_REPO_API = "https://api.github.com/repos/{owner}/{repo}"
PLACEHOLDER_RE = re.compile(
    r"code\s+(?:will\s+be|coming\s+soon|to\s+be)\s+released|"
    r"upon\s+acceptance|"
    r"implementation\s+(?:will\s+be|coming)|"
    r"stay\s+tuned|"
    r"not\s+(?:yet\s+)?(?:available|released)",
    re.I,
)
CODE_EXTS = {".py", ".ipynb", ".sh", ".cpp", ".cc", ".cu", ".c", ".h", ".hpp", ".java", ".js", ".ts", ".go", ".rs"}
MARKER_FILES = {
    "requirements.txt",
    "environment.yml",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
    "package.json",
    "makefile",
    "dockerfile",
    "train.py",
    "main.py",
    "infer.py",
    "run.py",
}


@dataclass
class RepoReport:
    url: str
    owner: str
    repo: str
    exists: bool = False
    stars: int | None = None
    default_branch: str | None = None
    is_empty_shell: bool = True
    has_code: bool = False
    has_markers: bool = False
    code_files: list[str] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def status(self) -> str:
        if self.error and not self.exists:
            return "unreachable"
        if not self.exists:
            return "missing"
        if self.has_code or self.has_markers:
            return "real_code"
        return "empty_shell"


@dataclass
class CodeStatus:
    urls: list[str] = field(default_factory=list)
    repos: list[RepoReport] = field(default_factory=list)
    placeholder_text: bool = False
    summary: str = ""
    spoken_intro: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _clean_repo(repo: str) -> str:
    repo = (repo or "").strip()
    if repo.lower().endswith(".git"):
        repo = repo[:-4]
    return repo.rstrip("./")


def extract_github_urls(text: str) -> list[str]:
    urls = []
    for m in GITHUB_RE.finditer(text):
        owner, repo = m.group("owner"), _clean_repo(m.group("repo"))
        if not owner or not repo:
            continue
        urls.append(f"https://github.com/{owner}/{repo}")
    return list(dict.fromkeys(urls))


def extract_hf_paper_ids(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"huggingface\.co/papers/(\d{4}\.\d{4,5})", text, re.I)))


def looks_like_placeholder(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text or ""))


def parse_github_repo(url: str) -> tuple[str, str] | None:
    m = GITHUB_RE.search(url.strip())
    if not m:
        return None
    owner, repo = m.group("owner"), _clean_repo(m.group("repo"))
    if not owner or not repo:
        return None
    return owner, repo


def inspect_repo(url: str, *, token: str | None = None, timeout: float = 20.0) -> RepoReport:
    """Query GitHub API for tree + stars and classify the repo."""
    parsed = parse_github_repo(url)
    if not parsed:
        return RepoReport(url=url, owner="", repo="", error="bad_url")
    owner, repo = parsed
    report = RepoReport(url=f"https://github.com/{owner}/{repo}", owner=owner, repo=repo)
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "paper2youtube/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        meta = _get_json(GITHUB_REPO_API.format(owner=owner, repo=repo), headers, timeout)
        report.exists = True
        report.stars = meta.get("stargazers_count")
        report.default_branch = meta.get("default_branch") or "main"
    except HTTPError as exc:
        if exc.code == 404:
            report.exists = False
            report.error = "not_found"
            return report
        report.error = f"http_{exc.code}"
        return report
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        report.error = f"url_error:{reason}"
        return report

    try:
        tree = _get_json(GITHUB_TREE_API.format(owner=owner, repo=repo), headers, timeout)
    except Exception as exc:
        report.error = f"tree_failed:{exc}"
        return report

    paths = [item.get("path", "") for item in tree.get("tree", []) if item.get("type") == "blob"]
    report.top_level = sorted({p.split("/")[0] for p in paths if p})
    code_files = [
        p
        for p in paths
        if any(p.lower().endswith(ext) for ext in CODE_EXTS)
    ]
    markers = [p for p in paths if path_basename(p).lower() in MARKER_FILES]
    report.code_files = code_files[:30]
    report.has_code = len(code_files) > 0
    report.has_markers = len(markers) > 0
    report.is_empty_shell = not (report.has_code or report.has_markers)
    return report


def path_basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def hf_paper_github_urls(arxiv_id: str, *, timeout: float = 20.0) -> list[str]:
    """Pull GitHub links from Hugging Face Papers API (PwC replacement)."""
    url = HF_PAPER_API.format(arxiv_id=arxiv_id)
    try:
        data = _get_json(url, {"User-Agent": "paper2youtube/0.1"}, timeout)
    except Exception:
        return []
    blob = json.dumps(data)
    return extract_github_urls(blob)


def check_code(
    markdown: str,
    *,
    arxiv_id: str | None = None,
    token: str | None = None,
    timeout: float = 20.0,
    inspect: bool = True,
    extra_urls: list[str] | None = None,
) -> CodeStatus:
    """Full code-availability report + spoken intro sentence."""
    urls = extract_github_urls(markdown)
    if extra_urls:
        for u in extra_urls:
            for found in extract_github_urls(u):
                urls.append(found)
            if parse_github_repo(u):
                urls.append(u)
        urls = list(dict.fromkeys(urls))
    if arxiv_id:
        urls = list(dict.fromkeys(urls + hf_paper_github_urls(arxiv_id, timeout=timeout)))

    status = CodeStatus(
        urls=urls,
        placeholder_text=looks_like_placeholder(markdown),
    )

    if inspect:
        for u in urls:
            if parse_github_repo(u):
                status.repos.append(inspect_repo(u, token=token, timeout=timeout))

    status.summary = _summarize(status)
    status.spoken_intro = _spoken(status)
    return status


def _summarize(status: CodeStatus) -> str:
    if not status.urls:
        return "no_code_links"
    real = [r for r in status.repos if r.status == "real_code"]
    shells = [r for r in status.repos if r.status == "empty_shell"]
    if real:
        return "real_code"
    if shells or status.placeholder_text:
        return "placeholder_only"
    return "links_unverified"


def _spoken(status: CodeStatus) -> str:
    """Opening line for the audio track (zh-TW friendly, code-switching safe)."""
    real = [r for r in status.repos if r.status == "real_code"]
    shells = [r for r in status.repos if r.status == "empty_shell"]

    if real:
        best = max(real, key=lambda r: (r.stars or 0, len(r.code_files)))
        dirs = ", ".join(best.top_level[:6]) or "repo root"
        kinds = _code_kind_phrase(best)
        return (
            f"本篇論文已開源程式碼，主要 Repo 是 github.com/{best.owner}/{best.repo}，"
            f"目前約 {best.stars or 0} stars，{kinds}，核心檔案分布在：{dirs}。"
        )
    if shells or status.placeholder_text:
        detail = "作者雖然附了 GitHub 連結，但目前僅有空白 README 或佔位說明，尚未釋出實作程式碼。"
        if status.placeholder_text and not shells:
            detail = "內文提到程式碼將於日後釋出，目前尚未提供可運行的開源實作。"
        return f"注意：{detail}"
    if status.urls:
        return "論文附有程式碼連結，但目前無法確認 Repo 內容，建議自行點開查證。"
    return "本篇論文未提供開源程式碼連結，以下聚焦方法與實驗細節。"


def _code_kind_phrase(repo: RepoReport) -> str:
    names = " ".join(repo.code_files).lower()
    if ".ipynb" in names:
        return "含 notebook 實作"
    if ".py" in names:
        return "主要基於 Python（多為 PyTorch 生態）實作"
    if any(n.endswith((".cpp", ".cu", ".cc", ".c")) for n in repo.code_files):
        return "含 C++/CUDA 效能關鍵模組"
    return "提供可運行原始碼"


def _get_json(url: str, headers: dict, timeout: float):
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def code_status_to_md(status: CodeStatus) -> str:
    lines = ["## Code availability", ""]
    lines.append(f"- **summary:** `{status.summary}`")
    lines.append(f"- **placeholder text detected:** {status.placeholder_text}")
    lines.append(f"- **spoken intro:** {status.spoken_intro}")
    if status.urls:
        lines.append("- **urls:**")
        for u in status.urls:
            lines.append(f"  - {u}")
    for r in status.repos:
        lines.append(
            f"- **{r.url}** — status=`{r.status}` stars={r.stars} "
            f"code_files={len(r.code_files)} markers={r.has_markers}"
        )
        if r.code_files:
            lines.append(f"  - sample: {', '.join(r.code_files[:8])}")
    return "\n".join(lines) + "\n"