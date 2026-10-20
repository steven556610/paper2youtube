from paper2youtube.code_checker import (
    CodeStatus,
    RepoReport,
    check_code,
    code_status_to_md,
    extract_github_urls,
    inspect_repo,
    looks_like_placeholder,
    parse_github_repo,
    path_basename,
    _spoken,
)


def test_extract_github_urls():
    urls = extract_github_urls("see https://github.com/openai/whisper.git and https://github.com/openai/whisper")
    assert urls == ["https://github.com/openai/whisper"]


def test_parse_github_repo():
    assert parse_github_repo("https://github.com/foo/bar") == ("foo", "bar")
    assert parse_github_repo("https://github.com/foo/bar/tree/main") == ("foo", "bar")
    assert parse_github_repo("https://example.com/x") is None


def test_looks_like_placeholder():
    assert looks_like_placeholder("Code will be released upon acceptance.")
    assert not looks_like_placeholder("Our code is at github.com/a/b")


def test_spoken_real_code():
    status = CodeStatus(
        repos=[
            RepoReport(
                url="https://github.com/a/b",
                owner="a",
                repo="b",
                exists=True,
                stars=12,
                has_code=True,
                has_markers=True,
                is_empty_shell=False,
                code_files=["train.py", "models/net.py"],
                top_level=["models", "train.py"],
            )
        ],
        summary="real_code",
    )
    s = _spoken(status)
    assert "已開源" in s
    assert "a/b" in s


def test_spoken_shell():
    status = CodeStatus(
        repos=[
            RepoReport(
                url="https://github.com/a/b",
                owner="a",
                repo="b",
                exists=True,
                has_code=False,
                is_empty_shell=True,
                code_files=[],
                top_level=["README.md"],
            )
        ],
        placeholder_text=True,
        summary="placeholder_only",
    )
    s = _spoken(status)
    assert "空白" in s or "尚未" in s


def test_spoken_none():
    s = _spoken(CodeStatus(urls=[], summary="no_code_links"))
    assert "未提供" in s


def test_inspect_repo_offline_error(monkeypatch):
    # Force network failure — function should return a report, not raise.
    def boom(*a, **k):
        raise TimeoutError("no network")

    monkeypatch.setattr("paper2youtube.code_checker._get_json", boom)
    rep = inspect_repo("https://github.com/this-org-should-not-exist-xyz/nope-repo", timeout=0.2)
    assert isinstance(rep, RepoReport)
    assert rep.status in {"unreachable", "missing", "real_code", "empty_shell"}
    assert rep.error


def test_check_code_without_network_inspect_false():
    md = "Code: https://github.com/foo/bar. Code will be released upon acceptance."
    status = check_code(md, inspect=False, arxiv_id=None)
    assert status.placeholder_text is True
    assert "https://github.com/foo/bar" in status.urls
    assert status.spoken_intro


def test_code_status_to_md_roundtrip_dict():
    status = CodeStatus(urls=["https://github.com/a/b"], summary="real_code", spoken_intro="開源")
    d = status.to_dict()
    assert d["summary"] == "real_code"
    assert "Code availability" in code_status_to_md(status)


def test_path_basename():
    assert path_basename("models/net.py") == "net.py"
    assert path_basename("requirements.txt") == "requirements.txt"