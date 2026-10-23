"""Tests for the from-scratch toolkit (includes Hindi)."""
from backend.dsa import (BM25, JobQueue, LRUCache, TokenBucket, Trie, clean_headers_footers,
                         overlaps, page_for_offset, rrf, tokenize, top_k)


def test_tokenize_keeps_devanagari_matras():
    assert tokenize("शुल्क ₹500 है।") == ["शुल्क", "500", "है"]


def test_bm25_hindi_and_english():
    bm = BM25()
    bm.add(1, "विश्वविद्यालय की फीस कितनी है")
    bm.add(2, "hostel rules and fees")
    assert bm.search("फीस", 1)[0][0] == 1
    assert bm.search("hostel", 1)[0][0] == 2
    assert bm.search("zzz") == []


def test_top_k_heap():
    assert top_k([5, 1, 9, 3, 7], 3) == [9, 7, 5]
    assert top_k([], 3) == []


def test_lru():
    c = LRUCache(2)
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and len(c) == 2


def test_rrf_prefers_items_in_both_lists():
    assert rrf([["a", "b", "c"], ["c", "a", "d"]])[0][0] == "a"


def test_page_lookup_and_overlap():
    assert page_for_offset([0, 100, 200], 0) == 1
    assert page_for_offset([0, 100, 200], 100) == 2
    assert overlaps(0, 10, 9, 20) and not overlaps(0, 10, 10, 20)


def test_header_footer_cleaner():
    pages = [f"Header\nbody {i}\nPage footer" for i in range(4)]
    assert all("Header" not in p and "footer" not in p for p in clean_headers_footers(pages))


def test_token_bucket():
    tb = TokenBucket(2, 1.0)
    assert tb.allow("ip", 0) and tb.allow("ip", 0) and not tb.allow("ip", 0)
    assert tb.allow("ip", 1.5)


def test_queue_and_trie():
    q = JobQueue(); q.push(1); q.push(2)
    assert q.pop() == 1 and q.status[1] == "processing"
    t = Trie(); t.insert("Fee structure"); t.insert("Hostel")
    assert t.starts_with("fe") == ["Fee structure"]


def test_chunker_offsets_match_text():
    from backend.chunking import chunk_text
    t = ("यह पहला वाक्य है। यह दूसरा वाक्य है? " * 30) + "Last sentence here. " * 20
    for c, s, e in chunk_text(t, 200, 0.15):
        assert t[s:e] == c and len(c) <= 200
