"""Tests for language detection and SSRF helpers."""
import pytest

from backend.utils import check_url_safe, detect_language, is_public_ip


def test_language():
    assert detect_language("फीस कितनी है?") == "hi"
    assert detect_language("What is the fee?") == "en"
    assert detect_language("mujhe fees kya hai batao") == "hinglish"


@pytest.mark.parametrize("ip,ok", [("127.0.0.1", False), ("10.0.0.5", False),
                                   ("169.254.169.254", False), ("192.168.1.1", False), ("8.8.8.8", True)])
def test_ip_rules(ip, ok):
    assert is_public_ip(ip) is ok


@pytest.mark.parametrize("url", ["http://localhost/x", "http://127.0.0.1", "ftp://example.com", "http://10.1.1.1"])
def test_blocked_urls(url):
    with pytest.raises(ValueError):
        check_url_safe(url)
