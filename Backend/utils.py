"""Small helpers: logging, hashing, language detection, SSRF checks."""
import hashlib
import ipaddress
import logging
import re
import socket
import unicodedata
from urllib.parse import urlparse

HINGLISH_WORDS = {
    "kya", "hai", "hain", "kaise", "kaun", "kab", "kitna", "kitne", "mujhe", "mera", "meri",
    "aap", "nahi", "nahin", "ka", "ki", "ke", "ko", "se", "mein", "me", "kyun", "kyon",
    "batao", "bataiye", "karna", "kare", "hota", "hoga", "chahiye", "wala", "yeh", "ye", "woh",
}


def get_logger(name: str) -> logging.Logger:
    """Return a logger. Never log document text at INFO level."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return logging.getLogger(name)


def normalize(text: str) -> str:
    """NFC-normalize and collapse spaces."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def sha256_text(text: str) -> str:
    """Hash normalized text to detect duplicates."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def strip_control(text: str) -> str:
    """Remove control characters (prompt-injection hygiene), keep newlines/tabs."""
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c)[0] != "C")


def detect_language(text: str) -> str:
    """'hi' if mostly Devanagari, 'hinglish' if Latin letters with Hindi words, else 'en'.
    Limit: the Hinglish check is a simple word list, so short or mixed text can be wrong."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    dev = sum(1 for c in letters if "\u0900" <= c <= "\u097F")
    if dev / len(letters) > 0.3:
        return "hi"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    hits = sum(1 for w in words if w in HINGLISH_WORDS)
    return "hinglish" if words and hits >= 1 and hits / len(words) >= 0.2 else "en"


def is_public_ip(ip: str) -> bool:
    """True only for normal public IPs (blocks private, loopback, link-local)."""
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def check_url_safe(url: str) -> None:
    """Raise ValueError if the URL is not a safe public http(s) URL (SSRF defence).
    Call again for every redirect target."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Only http and https links are allowed.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve this address.") from exc
    for info in infos:
        if not is_public_ip(info[4][0]):
            raise ValueError("This address is not allowed (private or local network).")
