"""Reconciler tests: mapper translation, admission policy, and the engine's
core promises - idempotency, supersession-not-append, tombstones, quarantine
polarity, curated merges, slug determinism (ADR 0007).

The integration tests build synthetic raw records shaped exactly like the
landed SPARQL bindings (see `_payload`) and run the real pass over them; the
engine's promises are then checked against the real constraints, which is the
point - `uq_field_provenance_live` is what turns a buggy re-run into a loud
IntegrityError instead of silent duplication.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from carmanac.db.models import (
    Company,
    CompanyRoleAssignment,
    Country,
    ExternalId,
    FieldProvenance,
    RawRecord,
    ReconciledRecord,
    ReconciliationFlag,
)
from carmanac.ingest.wikidata.land import canonicalize, content_hash
from carmanac.reconcile import policy
from carmanac.reconcile.engine import run_companies_pass, slugify
from carmanac.reconcile.sources import wikidata
from carmanac.reconcile.sources.wikidata import map_record

_WD = "http://www.wikidata.org/entity/"


def _payload(
    qid: str,
    label: str | None = None,
    classes: tuple[str, ...] = ("Q786820",),
    description: str = "",
    inceptions: tuple[str, ...] = (),
    dissolutions: tuple[str, ...] = (),
    country_codes: tuple[str, ...] = (),
    countries: tuple[str, ...] = (),
    websites: tuple[str, ...] = (),
) -> dict:
    """A payload in the exact canonicalized shape land.py stores."""

    def lit(value: str) -> dict:
        return {"type": "literal", "value": value}

    binding = {
        "item": {"type": "uri", "value": f"{_WD}{qid}"},
        "itemLabel": lit(label if label is not None else qid),
        "itemDescription": lit(description),
        "classes": lit("|".join(f"{_WD}{c}" for c in classes)),
        "inceptions": lit("|".join(inceptions)),
        "dissolutions": lit("|".join(dissolutions)),
        "countryCodes": lit("|".join(country_codes)),
        "countries": lit("|".join(countries)),
        "websites": lit("|".join(websites)),
    }
    return canonicalize(binding)


def _land(db, source, qid: str, **kwargs) -> RawRecord:
    payload = _payload(qid, **kwargs)
    rec = RawRecord(
        source_id=source.id,
        external_id=qid,
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


# --- mapper ------------------------------------------------------------------


def test_mapper_full_payload():
    m = map_record(
        _payload(
            "Q26678",
            label="BMW",
            classes=("Q786820", "Q4830453"),
            description="German carmaker",
            inceptions=("1916-03-07T00:00:00Z",),
            country_codes=("DE",),
            websites=("https://www.bmw.com",),
        )
    )
    assert m is not None
    assert m.external_id == "Q26678"
    assert m.classes == frozenset({"Q786820", "Q4830453"})
    by_field = {a.field_name: a for a in m.assertions}
    assert by_field["name"].value == "BMW"
    assert by_field["summary"].value == "German carmaker"
    assert by_field["founded_year"].value == 1916
    assert by_field["country_id"].value == "DE"
    assert by_field["website"].value == "https://www.bmw.com"
    assert m.flag_requests == ()


def test_mapper_earliest_founding_wins_and_flags():
    """Rising Auto's shape (F4): two founding claims -> project the earliest,
    keep BOTH in the observed value, and ask for a multi_value flag."""
    m = map_record(
        _payload(
            "Q112162285",
            label="Rising Auto",
            inceptions=("2020-05-10T00:00:00Z", "2021-11-05T00:00:00Z"),
        )
    )
    founded = next(a for a in m.assertions if a.field_name == "founded_year")
    assert founded.value == 2020
    assert "2021-11-05" in founded.observed_value
    assert any(f.kind == "multi_value" and f.field_name == "founded_year" for f in m.flag_requests)


def test_mapper_bare_qid_label_means_no_name():
    m = map_record(_payload("Q288696"))  # label defaults to the QID itself
    assert m.name is None
    assert not any(a.field_name == "name" for a in m.assertions)


def test_mapper_multiple_countries_flag_not_assert():
    m = map_record(_payload("Q1", label="X", country_codes=("DE", "GB")))
    assert not any(a.field_name == "country_id" for a in m.assertions)
    assert any(f.kind == "multi_value" and f.field_name == "country_id" for f in m.flag_requests)


# --- admission policy ---------------------------------------------------------


def test_admit_target_plus_allow():
    assert policy.classify({"Q786820", "Q4830453"}) == policy.ADMIT


def test_deny_beats_allow():
    """A dealership that is also a 'business' is still a dealership."""
    assert policy.classify({"Q786803", "Q4830453"}) == policy.DENY


def test_target_beats_deny():
    """Ford's shape, caught by the first live pass: `automobile manufacturer`
    + `holding company` is a marque with facets, not a holding shell."""
    assert policy.classify({"Q786820", "Q219577", "Q4830453"}) == policy.ADMIT


def test_unknown_class_quarantines():
    assert policy.classify({"Q4830453", "Q99999999999"}) == policy.QUARANTINE


def test_boilerplate_only_quarantines():
    """Policy v2, from Gaurav's review of the first live pass: 'business' +
    'enterprise' is zero CAR evidence - the seatbelt-supplier shape. v1
    admitted 2,175 of these."""
    assert policy.classify({"Q4830453", "Q6881511"}) == policy.QUARANTINE


def test_builder_class_admits():
    """The deliberately-open door: a coachbuilder admits (rolelessly) even
    with no target class."""
    assert policy.classify({"Q1734300", "Q4830453"}) == policy.ADMIT


def test_pin_admits_boilerplate_marque():
    """Peugeot's shape: P31 is literally just 'organization'; the fixture
    review vetted it by hand, and the pin encodes that judgment."""
    assert policy.classify({"Q43229"}, "Q6742") == policy.ADMIT
    assert policy.classify({"Q43229"}, "Q999999") == policy.QUARANTINE


def test_empty_class_set_quarantines():
    """Zero evidence is not vacuous truth (strict-admission polarity)."""
    assert policy.classify(frozenset()) == policy.QUARANTINE


def test_slugify():
    assert slugify("Škoda Auto", "Q29637") == "skoda-auto"
    assert slugify("BMW", "Q26678") == "bmw"
    assert slugify("一汽", "Q166885") == "q166885"  # no ASCII at all -> QID


# --- engine integration -------------------------------------------------------

pytestmark = pytest.mark.integration


@pytest.fixture()
def germany(db):
    country = Country(code="DE", name="Germany")
    db.add(country)
    db.commit()
    return country


def test_pass_creates_company_with_projection_and_role(db, wikidata_source, germany):
    _land(
        db,
        wikidata_source,
        "Q26678",
        label="BMW",
        classes=("Q786820", "Q4830453"),
        description="German carmaker",
        inceptions=("1916-03-07T00:00:00Z",),
        country_codes=("DE",),
        websites=("https://www.bmw.com",),
    )
    stats = run_companies_pass(db, wikidata)
    assert stats.admitted == 1 and stats.companies_created == 1

    company = db.scalars(select(Company)).one()
    assert (company.slug, company.name) == ("bmw", "BMW")
    assert company.founded_year == 1916
    assert company.country_id == germany.id
    assert company.website == "https://www.bmw.com"
    assert company.summary == "German carmaker"

    # every fact traces: assertions exist, each with a raw_record_id
    assertions = db.scalars(select(FieldProvenance)).all()
    assert {a.field_name for a in assertions} == {
        "name",
        "summary",
        "founded_year",
        "defunct_year",
        "country_id",
        "website",
    } - {"defunct_year"}
    assert all(a.raw_record_id is not None for a in assertions)

    # target class -> manufacturer role assertion with provenance
    role = db.scalars(select(CompanyRoleAssignment)).one()
    assert role.source_id == wikidata_source.id

    # QID landed in external_ids; record marked reconciled
    ext = db.scalars(select(ExternalId)).one()
    assert ext.external_id == "Q26678" and ext.company_id == company.id
    assert db.scalars(select(ReconciledRecord)).one().reconciler_version == (
        policy.RECONCILER_VERSION
    )


def test_pass_is_idempotent(db, wikidata_source, germany):
    _land(db, wikidata_source, "Q26678", label="BMW", country_codes=("DE",))
    run_companies_pass(db, wikidata)

    counts_before = {
        t: db.scalar(select(func.count()).select_from(t))
        for t in (Company, FieldProvenance, ExternalId, CompanyRoleAssignment, ReconciliationFlag)
    }
    stats = run_companies_pass(db, wikidata)
    counts_after = {t: db.scalar(select(func.count()).select_from(t)) for t in counts_before}
    assert counts_before == counts_after
    assert stats.assertions_inserted == 0 and stats.companies_created == 0


def test_changed_value_supersedes_not_appends(db, wikidata_source):
    _land(db, wikidata_source, "Q26678", label="BMW")
    run_companies_pass(db, wikidata)
    # the source now says something different (new current record)
    _land(db, wikidata_source, "Q26678", label="BMW AG")
    run_companies_pass(db, wikidata)

    live = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.field_name == "name",
            FieldProvenance.superseded_by.is_(None),
        )
    ).all()
    assert len(live) == 1 and live[0].observed_value == "BMW AG"
    history = db.scalars(select(FieldProvenance).where(FieldProvenance.field_name == "name")).all()
    assert len(history) == 2  # old row retained, repointed
    assert db.scalars(select(Company)).one().name == "BMW AG"


def test_field_gone_quiet_tombstones_and_unprojects(db, wikidata_source):
    _land(db, wikidata_source, "Q26678", label="BMW", websites=("https://bmw.com",))
    run_companies_pass(db, wikidata)
    assert db.scalars(select(Company)).one().website == "https://bmw.com"

    _land(db, wikidata_source, "Q26678", label="BMW")  # website claim deleted
    run_companies_pass(db, wikidata)

    live = db.scalars(
        select(FieldProvenance).where(
            FieldProvenance.field_name == "website",
            FieldProvenance.superseded_by.is_(None),
        )
    ).one()
    assert live.observed_value is None  # the tombstone, dating the retraction
    assert db.scalars(select(Company)).one().website is None


def test_unknown_class_quarantines_with_flag_no_company(db, wikidata_source):
    rec = _land(
        db, wikidata_source, "Q431289000", label="Mystery Org", classes=("Q15634581",)
    )  # "design company": deliberately unlisted
    stats = run_companies_pass(db, wikidata)
    assert stats.quarantined == 1
    assert db.scalar(select(func.count()).select_from(Company)) == 0

    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.kind == "admission_review"
    assert flag.raw_record_id == rec.id
    assert "Q15634581" in flag.detail["classes"]

    # re-run: the open flag is not duplicated
    run_companies_pass(db, wikidata)
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 1


def test_admission_dismisses_stale_quarantine_flag(db, wikidata_source):
    """Italdesign's shape from the first live pass: quarantined once, then the
    situation resolves (payload or policy change) - the open flag must be
    dismissed, not left asking a question nobody needs answered."""
    _land(db, wikidata_source, "Q283754", label="Italdesign", classes=("Q15634581",))
    run_companies_pass(db, wikidata)
    flag = db.scalars(select(ReconciliationFlag)).one()
    assert flag.status == "open"

    _land(
        db, wikidata_source, "Q283754", label="Italdesign", classes=("Q15634581", "Q786820")
    )  # Wikidata gains the target class
    stats = run_companies_pass(db, wikidata)
    assert stats.flags_dismissed == 1
    db.expire_all()
    assert flag.status == "dismissed" and flag.resolved_at is not None
    assert db.scalars(select(Company)).one().name == "Italdesign"


def test_changed_payload_does_not_duplicate_open_quarantine_flag(db, wikidata_source):
    """Record-scoped flags dedupe on EXTERNAL id: a changed payload lands a
    new raw row for the same entity, and the still-open question is not asked
    twice."""
    _land(db, wikidata_source, "Q1", label="Mystery", classes=("Q15634581",))
    run_companies_pass(db, wikidata)
    _land(db, wikidata_source, "Q1", label="Mystery Ltd", classes=("Q15634581",))
    run_companies_pass(db, wikidata)
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 1


def test_denied_waits_in_raw_without_flag(db, wikidata_source):
    _land(db, wikidata_source, "Q99", label="Joe's Car Wash", classes=("Q130639530", "Q4830453"))
    stats = run_companies_pass(db, wikidata)
    assert stats.denied == 1
    assert db.scalar(select(func.count()).select_from(Company)) == 0
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 0
    assert db.scalar(select(func.count()).select_from(ReconciledRecord)) == 1


def test_business_only_admits_without_role(db, wikidata_source):
    """Singer's shape: boilerplate classes but a PINNED entity (fixture-vetted)
    admits - with no target class, no manufacturer role. A builder, not a
    make."""
    _land(db, wikidata_source, "Q55633247", label="Singer Vehicle Design", classes=("Q4830453",))
    run_companies_pass(db, wikidata)
    assert db.scalars(select(Company)).one().name == "Singer Vehicle Design"
    assert db.scalar(select(func.count()).select_from(CompanyRoleAssignment)) == 0


def test_curated_merge_one_company_many_external_ids(db, wikidata_source):
    """Bugatti's shape: the canonical record creates the company and asserts
    facts; member records attach identity only."""
    _land(db, wikidata_source, "Q27401", label="Bugatti", inceptions=("1909-01-01T00:00:00Z",))
    _land(
        db,
        wikidata_source,
        "Q1002267",
        label="Bugatti Automobili S.p.A.",
        inceptions=("1987-01-01T00:00:00Z",),
    )
    _land(db, wikidata_source, "Q2308012", label="Bugatti Automobiles S.A.S.")
    stats = run_companies_pass(db, wikidata)

    company = db.scalars(select(Company)).one()  # ONE company
    assert company.name == "Bugatti"
    assert company.founded_year == 1909  # member's 1987 never asserted
    ids = set(db.scalars(select(ExternalId.external_id)))
    assert ids == {"Q27401", "Q1002267", "Q2308012"}
    assert stats.merged_members == 2

    run_companies_pass(db, wikidata)  # merge handling is idempotent too
    assert db.scalar(select(func.count()).select_from(ExternalId)) == 3


def test_implausible_single_claim_projects_and_flags(db, wikidata_source):
    """The AMG shape (policy v3): ONE founding claim, obviously wrong, no
    disagreement for multi_value to catch. The value still projects (§6.4)
    but an implausible_value flag records the suspicion."""
    _land(
        db, wikidata_source, "Q1370877", label="Mercedes-AMG", inceptions=("1812-06-01T00:00:00Z",)
    )
    run_companies_pass(db, wikidata)

    company = db.scalars(select(Company)).one()
    assert company.founded_year == 1812  # still projects, tentatively
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "implausible_value")
    ).one()
    assert flag.field_name == "founded_year"
    assert flag.company_id == company.id

    run_companies_pass(db, wikidata)  # open flag not duplicated
    assert db.scalar(select(func.count()).select_from(ReconciliationFlag)) == 1


def test_defunct_before_founded_flags(db, wikidata_source):
    _land(
        db,
        wikidata_source,
        "Q2",
        label="Backwards Motors",
        inceptions=("1990-01-01T00:00:00Z",),
        dissolutions=("1950-01-01T00:00:00Z",),
    )
    run_companies_pass(db, wikidata)
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.kind == "implausible_value")
    ).one()
    assert flag.field_name == "defunct_year"


def test_slug_collision_gets_qid_suffix(db, wikidata_source):
    """Two distinct marques, one name: ascending-QID order gives the lower QID
    the bare slug, deterministically (ADR 0007 §1/§7)."""
    _land(db, wikidata_source, "Q100", label="Eagle", classes=("Q786820",))
    _land(db, wikidata_source, "Q200", label="Eagle", classes=("Q786820",))
    run_companies_pass(db, wikidata)
    slugs = set(db.scalars(select(Company.slug)))
    assert slugs == {"eagle", "eagle-q200"}
