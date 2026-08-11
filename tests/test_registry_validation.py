"""Stale slug-pair registry keys fail loud (ADR 0019 §3).

A registry entry that no longer resolves against live pairs is a recorded
human judgment about to be silently disarmed - the negative case would
re-arm the exact match a human dismissed - so the consuming pass must
refuse to run, not warn and drift.
"""

from __future__ import annotations

import pytest

from carmanac.db.models import Company, Model, Source
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import validate_registry_pairs
from carmanac.reconcile.wikipedia_sections_pass import _SectionsPass

pytestmark = pytest.mark.integration


@pytest.fixture()
def spine(db):
    company = Company(slug="bmw", name="BMW")
    db.add(company)
    db.flush()
    db.add(Model(company_id=company.id, slug="z4", name="Z4"))
    db.commit()


def test_empty_models_table_skips_validation(db, monkeypatch):
    monkeypatch.setattr(policy, "SECTION_ARTICLE_MODELS", {"Q1": "gone/gone"})
    validate_registry_pairs(db)  # fresh clone / test DB: nothing to disarm


def test_resolving_entries_pass(db, spine, monkeypatch):
    monkeypatch.setattr(policy, "SECTION_ARTICLE_MODELS", {"Q1": "bmw/z4"})
    monkeypatch.setattr(policy, "WIKIDATA_MODEL_MATCHES", {"Q2": "bmw/z4"})
    monkeypatch.setattr(policy, "WIKIDATA_MODEL_NEGATIVES", frozenset({("Q3", "bmw/z4")}))
    validate_registry_pairs(db)


@pytest.mark.parametrize(
    ("registry", "value"),
    [
        ("SECTION_ARTICLE_MODELS", {"Q1": "bmw/stale"}),
        ("WIKIDATA_MODEL_MATCHES", {"Q2": "renamed-co/z4"}),
        ("WIKIDATA_MODEL_NEGATIVES", frozenset({("Q3", "bmw/stale")})),
    ],
)
def test_stale_entry_aborts_naming_the_entry(db, spine, monkeypatch, registry, value):
    monkeypatch.setattr(policy, registry, value)
    with pytest.raises(RuntimeError, match=registry):
        validate_registry_pairs(db)


def test_sections_pass_refuses_to_construct_on_stale_key(db, spine, monkeypatch):
    db.add(Source(name="Wikipedia (English)", tier=2, base_url="https://en.wikipedia.org"))
    db.add(Source(name="Wikidata", tier=1, base_url="https://www.wikidata.org"))
    db.commit()
    monkeypatch.setattr(policy, "SECTION_ARTICLE_MODELS", {"Q1": "bmw/renamed-away"})
    with pytest.raises(RuntimeError, match="SECTION_ARTICLE_MODELS"):
        _SectionsPass(db)
