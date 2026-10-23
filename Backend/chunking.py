"""Sentence-aware sliding-window chunker. Every chunk keeps character offsets."""
import re

SENT_END = re.compile(r"(?<=[.?!।])\s+")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of each sentence. Splits on . ? ! and the Hindi danda।"""
    spans, start = [], 0
    for m in SENT_END.finditer(text):
        if m.start() > start:
            spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def chunk_text(text: str, size: int = 500, overlap: float = 0.15) -> list[tuple[str, int, int]]:
    """Group sentences into chunks of about `size` chars with `overlap` fraction repeated.
    Long sentences are cut hard at `size`. Returns (chunk_text, start, end)."""
    pieces: list[tuple[int, int]] = []
    for s, e in split_sentences(text):
        while e - s > size:
            pieces.append((s, s + size))
            s += size
        pieces.append((s, e))
    chunks, i = [], 0
    keep = int(size * overlap)
    while i < len(pieces):
        j, start = i, pieces[i][0]
        while j < len(pieces) and pieces[j][1] - start <= size:
            j += 1
        j = max(j, i + 1)
        end = pieces[j - 1][1]
        chunks.append((text[start:end], start, end))
        if j >= len(pieces):
            break
        nxt = j  # step back to repeat the last sentences worth `keep` chars
        while nxt - 1 > i and end - pieces[nxt - 1][0] <= keep:
            nxt -= 1
        i = nxt if nxt > i else j
    return chunks
