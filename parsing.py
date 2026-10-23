"""Read PDF / DOCX / TXT into clean text plus page start offsets."""
import re
from pathlib import Path

from backend.dsa import clean_headers_footers
from backend.utils import get_logger, normalize

log = get_logger("parsing")
GARBLED_WARNING = "Text extraction looks broken. Try OCR."


def clean_page(text: str) -> str:
    """Fix hyphenated line breaks and extra spaces, keep paragraph breaks."""
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def looks_garbled(text: str) -> bool:
    """True if there are many replacement characters or very few letters."""
    if len(text) < 50:
        return False
    bad = text.count("\ufffd") / len(text)
    letters = sum(1 for c in text if c.isalpha()) / len(text)
    return bad > 0.02 or letters < 0.35


def join_pages(pages: list[str]) -> tuple[str, list[int]]:
    """Join pages with a blank line; return text and each page's start offset."""
    offsets, parts, pos = [], [], 0
    for p in pages:
        offsets.append(pos)
        parts.append(p)
        pos += len(p) + 2
    return "\n\n".join(parts), offsets


def parse_pdf(path: Path) -> list[str]:
    """Extract text per page with PyMuPDF."""
    import fitz  # PyMuPDF
    with fitz.open(path) as doc:
        return [page.get_text("text") for page in doc]


def parse_docx(path: Path) -> list[str]:
    """Extract paragraphs from DOCX (treated as one page)."""
    import docx
    d = docx.Document(str(path))
    return ["\n".join(p.text for p in d.paragraphs)]


def parse_txt(path: Path) -> list[str]:
    """Read text with encoding fallback."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return [raw.decode(enc)]
        except UnicodeError:
            continue
    return [raw.decode("utf-8", errors="replace")]


def parse_file(path: Path) -> tuple[str, list[int], str | None]:
    """Parse a file. Returns (full_text, page_offsets, warning)."""
    ext = path.suffix.lower()
    pages = {".pdf": parse_pdf, ".docx": parse_docx, ".txt": parse_txt}[ext](path)
    pages = [clean_page(p) for p in clean_headers_footers(pages)]
    text, offsets = join_pages(pages)
    warning = GARBLED_WARNING if looks_garbled(text) else None
    if warning and ext == ".pdf":
        ocr = try_ocr(path)  # optional fallback
        if ocr:
            text, offsets = join_pages([clean_page(p) for p in ocr])
            warning = None if not looks_garbled(text) else warning
    if not normalize(text):
        raise ValueError("No text found in this file. If it is a scan, install Tesseract for OCR.")
    return text, offsets, warning


def try_ocr(path: Path) -> list[str] | None:
    """Optional OCR (Tesseract eng+hin). Returns None if not installed."""
    try:
        import fitz
        import pytesseract
        from PIL import Image
        import io
        out = []
        with fitz.open(path) as doc:
            for page in doc:
                img = Image.open(io.BytesIO(page.get_pixmap(dpi=200).tobytes("png")))
                out.append(pytesseract.image_to_string(img, lang="eng+hin", timeout=60))
        return out
    except (ImportError, RuntimeError, OSError) as exc:
        log.info("OCR not available: %s", type(exc).__name__)
        return None
