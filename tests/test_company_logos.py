"""Company-logo landing and reconciliation (ADR 0021)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from carmanac.api.queries import root_index
from carmanac.db.models import (
    Company,
    ExternalId,
    MediaAsset,
    MediaAttachment,
    Model,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.company_logos import (
    COMPANY_LOGOS_QUERY,
    SWEEP_MARKER,
    land_company_logos,
    target_qids,
)
from carmanac.reconcile import policy
from carmanac.reconcile.company_logos_pass import _selected_files, run_company_logos_pass

pytestmark = pytest.mark.integration

_ENTITY = "http://www.wikidata.org/entity/"
_STATEMENT = "http://www.wikidata.org/entity/statement/"
_RANK = "http://wikiba.se/ontology#"
_FILE = "http://commons.wikimedia.org/wiki/Special:FilePath/"


class FakeSparqlClient:
    def __init__(self, bindings: list[dict[str, Any]]) -> None:
        self.bindings = bindings
        self.endpoint = "fake://wikidata"

    def query(self, sparql: str) -> dict[str, Any]:
        asked = {token.removeprefix("wd:") for token in sparql.split() if token.startswith("wd:Q")}
        rows = [row for row in self.bindings if row["item"]["value"].rsplit("/", 1)[-1] in asked]
        return {"results": {"bindings": rows}}

    def close(self) -> None:
        raise AssertionError("an injected client must not be closed")


class FakeCommonsClient:
    def __init__(self, pages: dict[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.endpoint = "fake://commons"

    def fetch(self, filenames: list[str]) -> list[dict[str, Any]]:
        return [self.pages[name] for name in filenames]

    def close(self) -> None:
        raise AssertionError("an injected client must not be closed")


def _binding(
    qid: str,
    filename: str,
    *,
    statement: str = "s1",
    rank: str = "NormalRank",
    start: str | None = None,
    end: str | None = None,
    point: str | None = None,
) -> dict[str, Any]:
    row = {
        "item": {"type": "uri", "value": f"{_ENTITY}{qid}"},
        "statement": {"type": "uri", "value": f"{_STATEMENT}{statement}"},
        "rank": {"type": "uri", "value": f"{_RANK}{rank}"},
        "logo": {"type": "uri", "value": f"{_FILE}{filename}"},
    }
    for key, value in (("start", start), ("end", end), ("point", point)):
        if value:
            row[key] = {"type": "literal", "value": value}
    return row


def _page(
    filename: str,
    *,
    file_hash: str = "abc123",
    license_name: str | None = "Public domain",
    artist: str | None = "Example Motor Company",
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "AttributionRequired": {"value": "false"},
        "Restrictions": {"value": "trademarked"},
    }
    if license_name:
        metadata["LicenseShortName"] = {"value": license_name}
    if artist:
        metadata["Artist"] = {"value": f"<b>{artist}</b>"}
    return {
        "pageid": 1,
        "title": f"File:{filename}",
        "imageinfo": [
            {
                "url": f"https://upload.wikimedia.org/{filename}",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{filename}",
                "thumburl": f"https://upload.wikimedia.org/thumb/{filename}.png",
                "width": 1000,
                "height": 500,
                "size": 2048,
                "mime": "image/svg+xml",
                "sha1": file_hash,
                "extmetadata": metadata,
            }
        ],
    }


@pytest.fixture()
def logo_graph(db, wikidata_source):
    commons = db.scalar(select(Source).where(Source.name == "Wikimedia Commons"))
    if commons is None:
        commons = Source(name="Wikimedia Commons", tier=1, base_url="https://commons.wikimedia.org")
        db.add(commons)
    company = Company(name="Example Motor Company", slug="example")
    db.add(company)
    db.flush()
    db.add_all(
        [
            Model(company_id=company.id, name="Example One", slug="example-one"),
            ExternalId(company_id=company.id, source_id=wikidata_source.id, external_id="Q100"),
        ]
    )
    db.commit()
    return company, commons


def _land(db, bindings: list[dict[str, Any]], pages: dict[str, dict[str, Any]]):
    return land_company_logos(
        db,
        sparql_client=FakeSparqlClient(bindings),
        commons_client=FakeCommonsClient(pages),
    )


def test_wikidata_query_matches_companies_by_qid_only():
    assert "VALUES ?item" in COMPANY_LOGOS_QUERY
    assert "?item p:P154" in COMPANY_LOGOS_QUERY
    assert "label" not in COMPANY_LOGOS_QUERY.casefold()
    assert "OPTIONAL {{ ?item p:P154" not in COMPANY_LOGOS_QUERY


def test_landing_keeps_wikidata_and_commons_as_separate_raw_claims(db, wikidata_source, logo_graph):
    _, commons = logo_graph
    result = _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})

    assert (result.qids, result.files, result.wikidata_inserted, result.commons_inserted) == (
        1,
        1,
        1,
        1,
    )
    records = list(db.scalars(select(RawRecord).order_by(RawRecord.source_id)))
    assert {record.source_id for record in records} == {wikidata_source.id, commons.id}
    assert {record.payload["sweep"] for record in records} == {SWEEP_MARKER}
    wikidata_record = next(record for record in records if record.source_id == wikidata_source.id)
    assert wikidata_record.external_id == "Q100"
    assert next(record for record in records if record.source_id == commons.id).external_id == (
        "File:Example.svg"
    )

    second = _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    assert (second.wikidata_inserted, second.commons_inserted) == (0, 0)
    assert len(db.scalars(select(RawRecord)).all()) == 2


def test_unique_logo_attaches_with_both_provenances(db, wikidata_source, logo_graph):
    company, commons = logo_graph
    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.assets_created, stats.attachments_created, stats.flags_opened) == (1, 1, 0)
    asset = db.scalars(select(MediaAsset)).one()
    attachment = db.scalars(select(MediaAttachment)).one()
    assert (asset.source_id, attachment.source_id) == (commons.id, wikidata_source.id)
    assert attachment.company_id == company.id and attachment.role == "company_logo"
    assert asset.raw_record_id != attachment.raw_record_id
    assert asset.license == "Public domain"
    assert asset.attribution == "Example Motor Company"
    assert asset.rights_notice == "trademarked"


def test_root_inventory_reads_the_live_company_logo(db, wikidata_source, logo_graph):
    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    run_company_logos_pass(db, as_of=date(2026, 8, 19))

    company = root_index(db)["companies"][0]

    assert company["company_slug"] == "example"
    assert company["logo_url"] == "https://upload.wikimedia.org/thumb/Example.svg.png"


def test_company_match_is_qid_exact_even_when_names_are_equal(db, wikidata_source, logo_graph):
    company, _ = logo_graph
    namesake = Company(name=company.name, slug="example-namesake")
    db.add(namesake)
    db.flush()
    db.add_all(
        [
            Model(company_id=namesake.id, name="Namesake One", slug="namesake-one"),
            ExternalId(company_id=namesake.id, source_id=wikidata_source.id, external_id="Q200"),
        ]
    )
    db.commit()

    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    run_company_logos_pass(db, as_of=date(2026, 8, 19))

    attachments = db.scalars(select(MediaAttachment)).all()
    assert [attachment.company_id for attachment in attachments] == [company.id]


def test_curated_source_qid_supplies_only_the_target_company_logo(
    db, wikidata_source, logo_graph, monkeypatch
):
    company, _ = logo_graph
    source_company = Company(name="Example Holdings", slug="example-holdings")
    db.add(source_company)
    db.flush()
    db.add(
        ExternalId(
            company_id=source_company.id,
            source_id=wikidata_source.id,
            external_id="Q200",
        )
    )
    db.commit()
    monkeypatch.setitem(policy.COMPANY_LOGO_SOURCE_QIDS, "Q100", "Q200")

    assert target_qids(db) == ["Q100", "Q200"]
    _land(
        db,
        [
            _binding("Q100", "Group.svg", statement="group"),
            _binding("Q200", "Marque.svg", statement="marque"),
        ],
        {
            "Group.svg": _page("Group.svg", file_hash="group"),
            "Marque.svg": _page("Marque.svg", file_hash="marque"),
        },
    )
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.assets_created, stats.attachments_created) == (1, 1)
    asset = db.scalars(select(MediaAsset)).one()
    attachment = db.scalars(select(MediaAttachment)).one()
    evidence = db.get(RawRecord, attachment.raw_record_id)
    assert asset.title == "Marque.svg"
    assert attachment.company_id == company.id
    assert evidence.external_id == "Q200"


def test_curated_source_qid_must_already_be_known(db, logo_graph, monkeypatch):
    monkeypatch.setitem(policy.COMPANY_LOGO_SOURCE_QIDS, "Q100", "Q999")

    with pytest.raises(LookupError, match="Q999"):
        target_qids(db)


def test_identity_merge_member_does_not_contribute_a_company_logo(db, wikidata_source, logo_graph):
    company, _ = logo_graph
    db.add_all(
        [
            ExternalId(
                company_id=company.id,
                source_id=wikidata_source.id,
                external_id="Q27401",
            ),
            ExternalId(
                company_id=company.id,
                source_id=wikidata_source.id,
                external_id="Q1002267",
            ),
        ]
    )
    db.commit()

    _land(
        db,
        [
            _binding("Q27401", "Canonical.svg", statement="canonical"),
            _binding("Q1002267", "Member.svg", statement="member"),
        ],
        {
            "Canonical.svg": _page("Canonical.svg", file_hash="canonical"),
            "Member.svg": _page("Member.svg", file_hash="member"),
        },
    )
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.attachments_created, stats.waits_ambiguous) == (1, 0)
    assert db.scalars(select(MediaAsset)).one().title == "Canonical.svg"
    assert db.scalars(select(ReconciliationFlag)).all() == []


def test_multiple_current_files_flag_instead_of_using_response_order(
    db, wikidata_source, logo_graph
):
    company, _ = logo_graph
    pages = {name: _page(name, file_hash=name) for name in ("Black.svg", "Blue.svg")}
    _land(
        db,
        [
            _binding("Q100", "Blue.svg", statement="s2"),
            _binding("Q100", "Black.svg", statement="s1"),
        ],
        pages,
    )
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.waits_ambiguous, stats.flags_opened) == (1, 1)
    assert db.scalars(select(MediaAttachment)).all() == []
    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.company_id == company.id and flag.field_name == "company_logo"
    assert [candidate["file"] for candidate in flag.detail["candidates"]] == [
        "Black.svg",
        "Blue.svg",
    ]


def test_reviewed_file_resolves_a_multi_value_flag(db, logo_graph, monkeypatch):
    pages = {name: _page(name, file_hash=name) for name in ("Black.svg", "Blue.svg")}
    _land(
        db,
        [
            _binding("Q100", "Blue.svg", statement="s2"),
            _binding("Q100", "Black.svg", statement="s1"),
        ],
        pages,
    )
    run_company_logos_pass(db, as_of=date(2026, 8, 19))
    monkeypatch.setitem(policy.COMPANY_LOGO_FILES, "Q100", "Blue.svg")

    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.attachments_created, stats.flags_dismissed, stats.waits_ambiguous) == (1, 1, 0)
    assert db.scalars(select(MediaAsset)).one().title == "Blue.svg"
    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.status == "dismissed"
    assert flag.detail["resolution"] == "company_logo_attached"


def test_reviewed_file_does_not_override_the_selected_candidates(db, logo_graph, monkeypatch):
    monkeypatch.setitem(policy.COMPANY_LOGO_FILES, "Q100", "Missing.svg")
    _land(db, [_binding("Q100", "Current.svg")], {"Current.svg": _page("Current.svg")})

    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.attachments_created, stats.waits_ambiguous) == (1, 0)
    assert db.scalars(select(MediaAsset)).one().title == "Current.svg"


def test_missing_rights_metadata_does_not_block_a_logo(db, logo_graph):
    _land(
        db,
        [_binding("Q100", "Unknown.svg")],
        {"Unknown.svg": _page("Unknown.svg", license_name=None, artist=None)},
    )
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.assets_created, stats.attachments_created, stats.waits_metadata) == (1, 1, 0)
    asset = db.scalars(select(MediaAsset)).one()
    assert asset.license is None
    assert asset.attribution is None


def test_changed_commons_file_supersedes_asset_and_attachment(db, logo_graph):
    _land(
        db,
        [_binding("Q100", "Example.svg")],
        {"Example.svg": _page("Example.svg", file_hash="old")},
    )
    run_company_logos_pass(db, as_of=date(2026, 8, 19))

    _land(
        db,
        [_binding("Q100", "Example.svg")],
        {"Example.svg": _page("Example.svg", file_hash="new")},
    )
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    assert (stats.assets_superseded, stats.attachments_superseded) == (1, 1)
    assets = db.scalars(select(MediaAsset).order_by(MediaAsset.id)).all()
    attachments = db.scalars(select(MediaAttachment).order_by(MediaAttachment.id)).all()
    assert assets[0].superseded_by == assets[1].id and assets[1].superseded_by is None
    assert attachments[0].superseded_by == attachments[1].id
    assert attachments[1].superseded_by is None


def test_withdrawn_p154_retires_the_current_attachment(db, logo_graph):
    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    run_company_logos_pass(db, as_of=date(2026, 8, 19))

    _land(db, [], {})
    stats = run_company_logos_pass(db, as_of=date(2026, 8, 19))

    attachment = db.scalars(select(MediaAttachment)).one()
    assert (stats.attachments_retired, stats.waits_no_logo) == (1, 1)
    assert attachment.superseded_by == attachment.id
    assert db.scalar(select(MediaAttachment).where(MediaAttachment.superseded_by.is_(None))) is None


def test_company_logo_role_rejects_a_model_attachment(db, wikidata_source, logo_graph):
    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    run_company_logos_pass(db, as_of=date(2026, 8, 19))
    asset = db.scalars(select(MediaAsset)).one()
    model = db.scalars(select(Model)).one()
    record = db.scalars(
        select(RawRecord).where(
            RawRecord.source_id == wikidata_source.id,
            RawRecord.payload.op("->>")("sweep") == SWEEP_MARKER,
        )
    ).one()
    db.add(
        MediaAttachment(
            media_asset_id=asset.id,
            model_id=model.id,
            role="company_logo",
            source_id=wikidata_source.id,
            raw_record_id=record.id,
            scraped_at=record.last_seen_at,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_attached_media_asset_cannot_be_deleted(db, logo_graph):
    _land(db, [_binding("Q100", "Example.svg")], {"Example.svg": _page("Example.svg")})
    run_company_logos_pass(db, as_of=date(2026, 8, 19))

    db.delete(db.scalars(select(MediaAsset)).one())
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_rank_and_time_qualifiers_select_only_a_current_logo():
    statements = [
        {"file": "Old.svg", "rank": "preferred", "starts": [], "ends": ["2020-01-01"]},
        {"file": "Current.svg", "rank": "normal", "starts": ["2021-01-01"], "ends": []},
        {
            "file": "Snapshot.svg",
            "rank": "normal",
            "starts": [],
            "ends": [],
            "points": ["2022-01-01"],
        },
    ]
    assert _selected_files(statements, date(2026, 8, 19)) == {"Current.svg"}


def test_latest_historical_logo_is_the_fallback_when_none_is_current():
    statements = [
        {
            "file": "PreferredButOlder.svg",
            "rank": "preferred",
            "starts": [],
            "ends": ["2020-01-01"],
        },
        {
            "file": "Latest.svg",
            "rank": "normal",
            "starts": [],
            "ends": ["2021-01-01"],
        },
        {
            "file": "Future.svg",
            "rank": "preferred",
            "starts": ["2027-01-01"],
            "ends": [],
        },
    ]

    assert _selected_files(statements, date(2026, 8, 19)) == {"Latest.svg"}
