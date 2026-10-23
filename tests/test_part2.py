"""Tests for importers (no network) and Lab metric helpers."""
import pytest

from backend.evaluation import (citation_accuracy, covered_length, metrics_for, percentile, retrieval_metrics)
from backend.importers import html_to_text, is_trusted, youtube_id


def test_trusted_domain_rules():
    allow = [".gov.in", ".nic.in"]
    assert is_trusted("https://www.india.gov.in/page", allow)
    assert is_trusted("https://services.nic.in/x", allow)
    assert not is_trusted("https://evilgov.in/x", allow)
    assert not is_trusted("https://gov.in.evil.com/x", allow)
    assert not is_trusted("https://example.com", allow)


def test_html_to_text_skips_menus_and_scripts():
    html = "<html><title>T</title><nav>MENU</nav><script>bad()</script><p>" + "Real content here. " * 20 + "</p></html>"
    title, text = html_to_text(html)
    assert title == "T" and "Real content" in text and "MENU" not in text and "bad()" not in text


def test_youtube_id():
    assert youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        youtube_id("https://example.com/video")


def test_metrics_helpers():
    assert percentile([1, 2, 3, 4], 50) == 2 and percentile([], 95) is None
    assert covered_length([(0, 10), (5, 15)]) == 15
    p = [{"source_id": "s", "start": 0, "end": 100, "rank": 2}]
    m = retrieval_metrics(p, "s", 50, 150)
    assert m["hit"] == 1 and m["rr"] == 0.5 and abs(m["recall"] - 0.5) < 1e-9
    assert retrieval_metrics(p, "other", 50, 150)["hit"] == 0
    assert citation_accuracy([1], p, "s", 50, 150) == 1.0


def test_refusal_rate_counts_only_unanswerable():
    rows = [dict(refuse_q=True, hit=None, recall=None, rr=None, correct=1, faithful=1, cite_acc=None, latency_ms=1, tokens=0, cost=0),
            dict(refuse_q=True, hit=None, recall=None, rr=None, correct=0, faithful=1, cite_acc=None, latency_ms=1, tokens=0, cost=0)]
    assert metrics_for(rows)["correct_refusal_rate"] == 0.5
