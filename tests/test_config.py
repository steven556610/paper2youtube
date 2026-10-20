from paper2youtube.config import Settings, ensure_workdir, load_settings


def test_settings_defaults():
    s = Settings()
    assert s.base_url.startswith("http")
    assert s.model
    assert s.vlm_model == s.model
    assert s.lang == "zh-TW"


def test_load_settings_overrides(monkeypatch):
    monkeypatch.setenv("PAPER2YOUTUBE_MODEL", "test-model")
    monkeypatch.setenv("PAPER2YOUTUBE_LANG", "zh-TW")
    s = load_settings(model="override-model")
    assert s.model == "override-model"
    assert s.lang == "zh-TW"


def test_has_llm_false_without_key(monkeypatch):
    monkeypatch.delenv("PAPER2YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    s = load_settings()
    assert s.has_llm is False


def test_ensure_workdir(tmp_path):
    wd = ensure_workdir(tmp_path / "lit")
    assert (wd / "figures").is_dir()
    assert (wd / "slides").is_dir()
    assert (wd / "audio").is_dir()
    assert (wd / "source").is_dir()