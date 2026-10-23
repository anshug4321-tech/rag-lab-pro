"""Injection documents must be wrapped as data and control characters removed."""
from pathlib import Path

from backend.rag import build_prompt, verify_citations

FILES = sorted(Path(__file__).parent.glob("injection/*.txt"))


def test_context_is_delimited_and_cleaned():
    assert len(FILES) == 3
    for f in FILES:
        text = f.read_text() + "\x00\x07"
        prompt = build_prompt("fee?", [{"rank": 1, "text": text}])
        assert prompt.startswith("<CONTEXT>") and "</CONTEXT>" in prompt
        assert "\x00" not in prompt and "\x07" not in prompt


def test_fake_citation_numbers_are_flagged():
    valid, invalid = verify_citations("It is free [1] and also [99].", 3)
    assert valid == [1] and invalid == [99]
