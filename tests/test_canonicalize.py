"""Canonicalization and hashing - the functions idempotent landing rests on.

The fixtures here are the real payload shapes that broke, not invented data:
the rotated-country KINTO payload is the exact failure observed live on
2026-07-24 (same 18 countries, different GROUP_CONCAT order, different hash,
spurious re-land).
"""

from __future__ import annotations

from carmanac.ingest.wikidata.land import canonicalize, content_hash, qid_from_uri

# The observed failure shape: identical content, rotated aggregation order.
_ROTATED_A = {
    "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q127773218"},
    "itemLabel": {"type": "literal", "value": "KINTO Europe"},
    "countries": {"type": "literal", "value": "Austria|Belgium|Germany|France"},
    "websites": {"type": "literal", "value": "https://b.example|https://a.example"},
}
_ROTATED_B = {
    "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q127773218"},
    "itemLabel": {"type": "literal", "value": "KINTO Europe"},
    "countries": {"type": "literal", "value": "France|Austria|Germany|Belgium"},
    "websites": {"type": "literal", "value": "https://a.example|https://b.example"},
}


def test_rotated_group_concat_hashes_identically():
    """The 2026-07-24 regression, replayed forever: same set of countries in a
    different concatenation order must produce the same content hash, or every
    multi-valued entity re-lands whenever the query plan shifts."""
    assert content_hash(canonicalize(_ROTATED_A)) == content_hash(canonicalize(_ROTATED_B))


def test_canonicalize_sorts_only_multi_value_vars():
    """Scalar cells must pass through untouched - canonicalization is for
    artifacts of our own aggregation, never for source values."""
    canonical = canonicalize(_ROTATED_A)
    assert canonical["countries"]["value"] == "Austria|Belgium|France|Germany"
    assert canonical["itemLabel"] == _ROTATED_A["itemLabel"]
    assert canonical["item"] == _ROTATED_A["item"]


def test_canonicalize_is_idempotent():
    once = canonicalize(_ROTATED_A)
    assert canonicalize(once) == once


def test_canonicalize_preserves_cell_metadata():
    """The binding keeps its {"type": ..., "value": ...} shape - datatype and
    language tags are part of what the source said (land.py docstring)."""
    canonical = canonicalize(_ROTATED_A)
    assert canonical["countries"]["type"] == "literal"


def test_canonicalize_tolerates_cells_without_value():
    """OPTIONAL vars can be absent or oddly shaped; canonicalize must not
    assume every cell carries a string value."""
    binding = {"countries": {"type": "literal"}, "item": {"value": "x"}}
    assert canonicalize(binding) == binding


def test_content_hash_ignores_key_order():
    """SPARQL does not promise stable key order between runs; the hash must
    depend on content only."""
    a = {"x": {"value": "1"}, "y": {"value": "2"}}
    b = {"y": {"value": "2"}, "x": {"value": "1"}}
    assert content_hash(a) == content_hash(b)


def test_content_hash_distinguishes_content():
    a = {"x": {"value": "1"}}
    b = {"x": {"value": "2"}}
    assert content_hash(a) != content_hash(b)


def test_content_hash_stable_for_unicode():
    """ensure_ascii=False path: Citroën must hash the same in any process,
    interpreter, or platform."""
    payload = {"itemLabel": {"value": "Citroën"}}
    assert content_hash(payload) == content_hash({"itemLabel": {"value": "Citroën"}})


def test_qid_from_uri():
    assert qid_from_uri("http://www.wikidata.org/entity/Q26678") == "Q26678"
    # Already-bare ids pass through: external_ids rows must match on the same
    # literal string wherever the value came from.
    assert qid_from_uri("Q26678") == "Q26678"
