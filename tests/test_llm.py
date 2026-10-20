from paper2youtube.llm import ScriptedLLM, extract_json


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    raw = "```json\n{\"a\": 2}\n```"
    assert extract_json(raw) == {"a": 2}


def test_extract_json_embedded():
    raw = 'Here you go:\n{"narration": "ok", "bullets": ["x"]}\nThanks'
    data = extract_json(raw)
    assert data["narration"] == "ok"


def test_scripted_llm_replies():
    llm = ScriptedLLM(replies=['{"narration": "hi"}'])
    assert llm.chat("p") == '{"narration": "hi"}'
    assert llm.chat_json("p2") == {}
    assert len(llm.calls) == 2


def test_data_url_from_file(tmp_path):
    from paper2youtube.llm import _data_url

    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    url = _data_url(p)
    assert url.startswith("data:image/png;base64,")