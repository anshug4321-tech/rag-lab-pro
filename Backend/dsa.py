"""From-scratch data structures and algorithms used by RAG Lab Pro.
Run self-test: python -m backend.dsa --selftest"""
import heapq
import math
import time
import unicodedata
from bisect import bisect_right
from collections import Counter, defaultdict, deque
from typing import Any, Hashable, Iterable, Optional


def tokenize(text: str) -> list[str]:
    """Split on whitespace/punctuation. We do NOT use \\w+ because it breaks Devanagari
    at matras (combining marks). A char is kept if it is a letter/digit or a Mark (category M).
    Time: O(n)."""
    tokens, cur = [], []
    for ch in unicodedata.normalize("NFC", text.lower()):
        if ch.isalnum() or unicodedata.category(ch)[0] == "M":
            cur.append(ch)
        elif cur:
            tokens.append("".join(cur))
            cur = []
    if cur:
        tokens.append("".join(cur))
    return tokens


class BM25:
    """BM25 with an inverted index: token -> {doc_id: term_frequency}.
    Build: O(total tokens). Search: O(sum of postings of query terms)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.postings: dict[str, dict[Hashable, int]] = defaultdict(dict)
        self.doc_len: dict[Hashable, int] = {}

    def add(self, doc_id: Hashable, text: str) -> None:
        """Add one document to the index."""
        toks = tokenize(text)
        self.doc_len[doc_id] = len(toks)
        for tok, tf in Counter(toks).items():
            self.postings[tok][doc_id] = tf

    def search(self, query: str, k: int = 10) -> list[tuple[Hashable, float]]:
        """Return top-k (doc_id, score), best first."""
        n = len(self.doc_len)
        if n == 0:
            return []
        avg = sum(self.doc_len.values()) / n
        scores: dict[Hashable, float] = defaultdict(float)
        for tok in set(tokenize(query)):
            plist = self.postings.get(tok)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            for doc_id, tf in plist.items():
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[doc_id] / avg)
                scores[doc_id] += idf * tf * (self.k1 + 1) / norm
        return top_k(scores.items(), k, key=lambda x: x[1])


def top_k(items: Iterable[Any], k: int, key=lambda x: x) -> list[Any]:
    """Best k items using a min-heap of size k. Time O(n log k), space O(k)."""
    heap: list[tuple[Any, int, Any]] = []
    for i, item in enumerate(items):
        entry = (key(item), -i, item)  # -i keeps order stable on ties
        if len(heap) < k:
            heapq.heappush(heap, entry)
        elif entry > heap[0]:
            heapq.heapreplace(heap, entry)
    return [e[2] for e in sorted(heap, reverse=True)]


class _Node:
    """Doubly linked list node for the LRU cache."""
    __slots__ = ("key", "val", "prev", "next")

    def __init__(self, key: Hashable = None, val: Any = None) -> None:
        self.key, self.val, self.prev, self.next = key, val, None, None


class LRUCache:
    """Least-Recently-Used cache: hash map + doubly linked list. get/put are O(1)."""

    def __init__(self, capacity: int) -> None:
        self.cap = max(1, capacity)
        self.map: dict[Hashable, _Node] = {}
        self.head, self.tail = _Node(), _Node()  # dummy ends
        self.head.next, self.tail.prev = self.tail, self.head

    def _remove(self, n: _Node) -> None:
        n.prev.next, n.next.prev = n.next, n.prev

    def _add_front(self, n: _Node) -> None:
        n.next, n.prev = self.head.next, self.head
        self.head.next.prev = n
        self.head.next = n

    def get(self, key: Hashable) -> Optional[Any]:
        """Return value and mark as recently used, or None."""
        n = self.map.get(key)
        if n is None:
            return None
        self._remove(n)
        self._add_front(n)
        return n.val

    def put(self, key: Hashable, val: Any) -> None:
        """Insert/update; evict the least recently used when full."""
        if key in self.map:
            self._remove(self.map[key])
        elif len(self.map) >= self.cap:
            lru = self.tail.prev
            self._remove(lru)
            del self.map[lru.key]
        n = _Node(key, val)
        self.map[key] = n
        self._add_front(n)

    def __len__(self) -> int:
        return len(self.map)


class JobQueue:
    """FIFO queue of ingestion jobs with status (queued/processing/ready/failed). O(1) ops."""

    def __init__(self) -> None:
        self.q: deque[Hashable] = deque()
        self.status: dict[Hashable, str] = {}

    def push(self, job_id: Hashable) -> None:
        self.q.append(job_id)
        self.status[job_id] = "queued"

    def pop(self) -> Optional[Hashable]:
        if not self.q:
            return None
        job_id = self.q.popleft()
        self.status[job_id] = "processing"
        return job_id

    def set_status(self, job_id: Hashable, status: str) -> None:
        self.status[job_id] = status


def rrf(rankings: list[list[Hashable]], k: int = 60) -> list[tuple[Hashable, float]]:
    """Reciprocal Rank Fusion: score = sum 1/(k + rank). Time O(total items)."""
    scores: dict[Hashable, float] = defaultdict(float)
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def page_for_offset(page_starts: list[int], offset: int) -> int:
    """Page number (1-based) for a character offset. Binary search: O(log pages)."""
    return max(1, bisect_right(page_starts, offset))


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True if [a_start, a_end) and [b_start, b_end) share any characters. O(1)."""
    return a_start < b_end and b_start < a_end


def clean_headers_footers(pages: list[str], min_pages: int = 3) -> list[str]:
    """Remove lines that appear on more than half of the pages (headers/footers).
    Hash-map line counts. Time O(total lines)."""
    if len(pages) < min_pages:
        return pages
    counts: Counter[str] = Counter()
    for p in pages:
        counts.update({ln.strip() for ln in p.splitlines() if ln.strip()})
    repeated = {ln for ln, c in counts.items() if c > len(pages) / 2}
    return ["\n".join(ln for ln in p.splitlines() if ln.strip() not in repeated) for p in pages]


class TokenBucket:
    """Rate limiter. Each key (IP) has a bucket that refills over time. O(1) per call."""

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.cap, self.rate = capacity, refill_per_sec
        self.state: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        """Take one token if available."""
        now = time.monotonic() if now is None else now
        tokens, last = self.state.get(key, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.rate)
        if tokens < 1:
            self.state[key] = (tokens, now)
            return False
        self.state[key] = (tokens - 1, now)
        return True


class Trie:
    """Prefix tree for autocomplete. Insert O(L); prefix search O(L + results)."""

    def __init__(self) -> None:
        self.children: dict[str, "Trie"] = {}
        self.words: list[str] = []  # original strings that end here

    def insert(self, word: str) -> None:
        node = self
        for ch in word.lower():
            node = node.children.setdefault(ch, Trie())
        node.words.append(word)

    def starts_with(self, prefix: str, limit: int = 5) -> list[str]:
        node = self
        for ch in prefix.lower():
            node = node.children.get(ch)
            if node is None:
                return []
        out: list[str] = []
        stack = [node]
        while stack and len(out) < limit:
            cur = stack.pop()
            out.extend(cur.words)
            stack.extend(cur.children.values())
        return out[:limit]


def _selftest() -> None:
    """Quick checks you can run without pytest."""
    assert tokenize("शुल्क ₹500 है।") == ["शुल्क", "500", "है"]
    bm = BM25()
    bm.add(1, "विश्वविद्यालय की फीस कितनी है")
    bm.add(2, "hostel rules and fees")
    assert bm.search("फीस", 1)[0][0] == 1
    assert top_k([5, 1, 9, 3], 2) == [9, 5]
    c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1
    assert rrf([["a", "b"], ["b", "a"]])[0][1] > 0
    assert page_for_offset([0, 100, 200], 150) == 2
    assert overlaps(0, 10, 5, 15) and not overlaps(0, 5, 5, 10)
    print("dsa selftest OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
