"""Import from a web link, a YouTube video, or official sources (allowlisted domains only)."""
import json
import re
from bisect import bisect_right
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlparse

from backend import config, settings as app_settings
from backend.utils import check_url_safe, get_logger

log = get_logger("importers")
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
HTTP_TIMEOUT = 15
UA = {"User-Agent": "RAGLabPro/1.0 (learning project)"}
SKIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript", "form", "svg", "iframe"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}


class _TextParser(HTMLParser):
    """Collect visible text and the <title>. Skips menus, scripts and footers."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, main text). Uses trafilatura if installed, else a simple parser."""
    p = _TextParser()
    p.feed(html)
    title = re.sub(r"\s+", " ", p.title).strip()
    try:
        import trafilatura
        good = trafilatura.extract(html)
        if good and len(good) > 200:
            return title, good
    except ImportError:
        pass
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return title, re.sub(r"\n\s*\n+", "\n\n", text).strip()


def fetch_url(url: str) -> tuple[str, str]:
    """Download a page safely. SSRF check runs on EVERY hop, including redirects.
    Known limit: DNS can change between our check and the connection (DNS rebinding)."""
    import httpx
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False, headers=UA) as client:
            for _ in range(MAX_REDIRECTS + 1):
                check_url_safe(url)
                with client.stream("GET", url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        continue
                    if r.status_code >= 400:
                        raise ValueError(f"The website returned error {r.status_code}.")
                    ctype = r.headers.get("content-type", "")
                    if "html" not in ctype and "text" not in ctype:
                        raise ValueError("Only web pages and text are supported. For a PDF, upload the file.")
                    data = bytearray()
                    for piece in r.iter_bytes():
                        data += piece
                        if len(data) > MAX_PAGE_BYTES:
                            raise ValueError("This page is too large (limit 5 MB).")
                    html = data.decode(r.encoding or "utf-8", errors="replace")
                title, text = html_to_text(html)
                if len(text) < 100:
                    raise ValueError("Could not find readable text on this page.")
                return title or urlparse(url).netloc, text
    except httpx.HTTPError as exc:
        raise ValueError("Could not reach this website (timeout or network error).") from exc
    raise ValueError("Too many redirects.")


# ---------- YouTube ----------
YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def youtube_id(url: str) -> str:
    """Extract the 11-character video id or raise ValueError."""
    m = YT_ID.search(url)
    if not m:
        raise ValueError("This does not look like a YouTube video link.")
    return m.group(1)


def youtube_transcript(url: str):
    """Return (title, text, seconds_for_offset). Prefers Hindi then English captions.
    Package: youtube-transcript-api (API changed between versions; both are handled)."""
    vid = youtube_id(url)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise ValueError("Install youtube-transcript-api to import YouTube videos.") from exc
    langs = ["hi", "en"]
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):  # older versions
            items = [(d["start"], d["text"]) for d in YouTubeTranscriptApi.get_transcript(vid, languages=langs)]
        else:  # newer versions
            items = [(s.start, s.text) for s in YouTubeTranscriptApi().fetch(vid, languages=langs)]
    except Exception as exc:  # the library raises many custom types; we turn them into one friendly message
        name = type(exc).__name__
        log.info("YouTube transcript failed: %s", name)
        if any(k in name for k in ("Disabled", "NotFound", "Unavailable")):
            raise ValueError("This video has no captions (Hindi or English), so it cannot be imported.") from exc
        raise ValueError("Could not get the transcript. Try again later or use another video.") from exc
    text, offsets, secs, pos = [], [], [], 0
    for start, line in items:
        line = line.replace("\n", " ").strip()
        offsets.append(pos)
        secs.append(float(start))
        text.append(line)
        pos += len(line) + 1
    full = " ".join(text)
    if len(full) < 50:
        raise ValueError("The transcript is too short to use.")
    return youtube_title(vid), full, lambda off: secs[max(0, bisect_right(offsets, off) - 1)]


def youtube_title(vid: str) -> str:
    """Get the title from YouTube's public oEmbed. Falls back to a plain name."""
    import httpx
    try:
        r = httpx.get("https://www.youtube.com/oembed", timeout=HTTP_TIMEOUT, headers=UA,
                      params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"})
        return r.json().get("title") or f"YouTube video {vid}"
    except (httpx.HTTPError, ValueError):
        return f"YouTube video {vid}"


# ---------- Official sources ----------
def trusted_domains() -> list[str]:
    """Allowlist from settings (suffixes like .gov.in or full domains)."""
    return app_settings.load().get("trusted_domains") or config.TRUSTED_DOMAINS


def is_trusted(url: str, allow: list[str] | None = None) -> bool:
    """True if the URL host ends with an allowed suffix (dot-aware, so evilgov.in does not match .gov.in)."""
    host = (urlparse(url).hostname or "").lower()
    for d in allow if allow is not None else trusted_domains():
        d = d.lower()
        if host == d.lstrip(".") or host.endswith(d if d.startswith(".") else "." + d):
            return True
    return False


def search_official(query: str, limit: int = 8) -> list[dict]:
    """Search, then keep ONLY allowlisted domains. Provider: Google Programmable Search
    (needs SEARCH_API_KEY and SEARCH_ENGINE_ID in .env). Swap this function to use another search API.
    The 'official_domain' flag means the DOMAIN is on your list. It does NOT mean the content is true."""
    import os
    import httpx
    key, cx = os.getenv("SEARCH_API_KEY", ""), os.getenv("SEARCH_ENGINE_ID", "")
    if not key or not cx:
        raise ValueError("Official search is not set up. Add SEARCH_API_KEY and SEARCH_ENGINE_ID to .env.")
    try:
        r = httpx.get("https://www.googleapis.com/customsearch/v1", timeout=HTTP_TIMEOUT,
                      params={"key": key, "cx": cx, "q": query, "num": 10})
        r.raise_for_status()
        items = r.json().get("items", [])
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("The search service did not answer. Try again later.") from exc
    out = []
    for it in items:
        if is_trusted(it.get("link", "")):
            meta = (it.get("pagemap", {}).get("metatags") or [{}])[0]
            out.append({"title": it.get("title", ""), "url": it["link"], "domain": urlparse(it["link"]).hostname,
                        "date": meta.get("article:published_time") or meta.get("date"), "official_domain": True})
    return out[:limit]
