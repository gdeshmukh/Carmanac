"""EPA attach pass tests (ADR 0014 §3-§4): the two bridges, the trim-parse
word boundary, group merging, 1:1 external ids, and re-run convergence."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from carmanac.db.models import (
    Configuration,
    ConfigurationAttribute,
    ExternalId,
    FieldProvenance,
    MatchDecision,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import content_hash
from carmanac.reconcile.epa_attach_pass import run_epa_attach_pass
from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass
from tests.test_vpic_models_pass import _land_model, _matched_make, vpic_source  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture()
def epa_source(db) -> Source:
    source = db.scalar(select(Source).where(Source.name == "EPA fueleconomy.gov"))
    if source is None:
        source = Source(name="EPA fueleconomy.gov", tier=1, base_url="https://www.fueleconomy.gov")
        db.add(source)
        db.commit()
    return source


def _land_vehicle(db, source, vid: int, make: str, model: str, year: int, **fields) -> RawRecord:
    payload = {
        "id": str(vid),
        "make": make,
        "model": model,
        "baseModel": fields.pop("baseModel", model),
        "year": str(year),
        **{k: str(v) for k, v in fields.items()},
    }
    rec = RawRecord(
        source_id=source.id,
        external_id=f"vehicle:{vid}",
        content_hash=content_hash(payload),
        payload=payload,
    )
    db.add(rec)
    db.commit()
    return rec


def _toyota_4runner(db, wikidata_source, vpic_source):  # noqa: F811
    _matched_make(db, wikidata_source, vpic_source, "Q53268", "Toyota", 448)
    _land_model(db, vpic_source, 1, "4Runner", 448, "TOYOTA")
    run_vpic_models_pass(db)


def test_exact_attach_creates_configuration(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """An exact model hit mints the period and the configuration, writes the
    1:1 external id, and lands column facts with provenance - generation NULL."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(
        db,
        epa_source,
        100,
        "Toyota",
        "4Runner",
        1999,
        drive="Rear-Wheel Drive",
        trany="Automatic 4-spd",
        displ="3.4",
        cylinders="6",
        fuelType1="Regular Gasoline",
        city08="16",
        highway08="19",
        comb08="17",
    )

    stats = run_epa_attach_pass(db)

    assert stats.attached_exact == 1 and stats.configurations_created == 1
    assert stats.periods_created == 1 and stats.external_ids_written == 1
    config = db.scalars(select(Configuration)).one()
    assert config.trim_name is None and config.generation_id is None
    assert config.engine_displacement_cc == 3400 and config.cylinders == 6
    assert float(config.mpg_combined) == 17
    assert config.slug == "toyota-4runner-1999-rwd"
    ext = db.scalars(select(ExternalId).where(ExternalId.external_id == "vehicle:100")).one()
    assert ext.configuration_id == config.id
    prov = db.scalars(
        select(FieldProvenance).where(FieldProvenance.configuration_id == config.id)
    ).all()
    assert {p.field_name for p in prov} >= {"engine_displacement_cc", "cylinders", "mpg_combined"}


def test_trim_parse_needs_word_boundary(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """'Range Rover' does not wear the as-filed 'Ranger': the prefix must end
    on a whole token, so the row flags instead of mis-attaching."""
    _matched_make(db, wikidata_source, vpic_source, "Q30988", "Ford", 460)
    _land_model(db, vpic_source, 2, "Ranger", 460, "FORD")
    run_vpic_models_pass(db)
    _land_vehicle(db, epa_source, 101, "Ford", "Range Rover", 1990, baseModel="Range Rover")

    stats = run_epa_attach_pass(db)

    assert stats.no_model_match == 1 and stats.configurations_created == 0
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.source_id == epa_source.id)
    ).one()
    assert flag.detail["reason"] == "no_model_match" and flag.detail["model"] == "Range Rover"


def test_trim_residue_and_base_model_rung(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """The AMG GT shape: baseModel names the as-filed model, the residue past
    the word-boundary prefix becomes the trim."""
    _matched_make(db, wikidata_source, vpic_source, "Q36008", "Mercedes-Benz", 449)
    _land_model(db, vpic_source, 3, "AMG GT", 449, "MERCEDES-BENZ")
    run_vpic_models_pass(db)
    _land_vehicle(db, epa_source, 102, "Mercedes-Benz", "AMG GT S Coupe", 2019, baseModel="AMG GT")

    stats = run_epa_attach_pass(db)

    assert stats.attached_base == 1
    config = db.scalars(select(Configuration)).one()
    assert config.trim_name == "S Coupe"
    decision = db.scalars(
        select(MatchDecision).where(MatchDecision.pass_name == "epa_attach")
    ).one()
    assert (decision.rung, decision.method) == ("2", "base_model")


def test_group_merges_and_conflicting_columns_stay_null(
    db,
    wikidata_source,
    vpic_source,  # noqa: F811
    epa_source,
):
    """An automatic and a manual of the same (model, year, trim, drive) are
    ONE configuration: no external ids (not 1:1), disagreeing columns NULL,
    agreeing columns written."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    common = dict(drive="Rear-Wheel Drive", displ="3.4", cylinders="6")
    _land_vehicle(db, epa_source, 103, "Toyota", "4Runner", 1999, trany="Automatic 4-spd", **common)
    _land_vehicle(db, epa_source, 104, "Toyota", "4Runner", 1999, trany="Manual 5-spd", **common)

    stats = run_epa_attach_pass(db)

    assert stats.attached_exact == 2 and stats.configurations_created == 1
    assert stats.external_ids_written == 0 and stats.column_conflicts == 1
    config = db.scalars(select(Configuration)).one()
    assert config.transmission_type_id is None
    assert config.engine_displacement_cc == 3400


def test_unbridged_make_waits_with_one_flag(db, vpic_source, epa_source):  # noqa: F811
    """Rows whose make no bridge places wait - one flag per distinct make
    string, not per row."""
    _land_vehicle(db, epa_source, 105, "Roush Performance", "Stage 3", 2015)
    _land_vehicle(db, epa_source, 106, "Roush Performance", "Stage 2", 2014)

    stats = run_epa_attach_pass(db)

    assert stats.waits_unbridged_make == 2 and stats.flags_opened == 1
    flag = db.scalars(
        select(ReconciliationFlag).where(ReconciliationFlag.source_id == epa_source.id)
    ).one()
    assert flag.detail["reason"] == "unbridged_make"


def test_rerun_converges(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(db, epa_source, 107, "Toyota", "4Runner", 1999, drive="Rear-Wheel Drive")
    run_epa_attach_pass(db)

    rerun = run_epa_attach_pass(db)

    assert rerun.configurations_created == 0 and rerun.configurations_existing == 1
    assert rerun.periods_created == 0 and rerun.external_ids_written == 0
    assert db.scalar(select(Configuration.id)) is not None


# --- ADR 0015: powertrain facts, not entities --------------------------------


def test_automated_manual_codes_assert_nothing(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """EPA's AM/AM-S codes cannot distinguish a single-clutch automated
    manual from a dual-clutch - no bucket word may erase that difference
    so the row asserts NO transmission type. AV codes
    are CVTs and map."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(db, epa_source, 200, "Toyota", "4Runner", 2020, trany="Automatic (AM-S7)")
    _land_vehicle(db, epa_source, 201, "Toyota", "4Runner", 2021, trany="Automatic (AV-S6)")

    run_epa_attach_pass(db)

    by_year = {c.catalogue_period.start_year: c for c in db.scalars(select(Configuration)).all()}
    assert by_year[2020].transmission_type_id is None
    from carmanac.db.models import TransmissionType

    cvt = db.scalar(select(TransmissionType.id).where(TransmissionType.code == "cvt"))
    assert by_year[2021].transmission_type_id == cvt


def test_sole_source_refresh_corrects_stale_column(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """The live correction (ADR 0015 §1): a configuration written under the
    old AM->automatic mapping is corrected on re-run - old assertion
    superseded by a NULL-observed successor, column NULLed."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    rec = _land_vehicle(db, epa_source, 202, "Toyota", "4Runner", 2020, trany="Automatic (AM-S7)")
    run_epa_attach_pass(db)
    config = db.scalars(select(Configuration)).one()
    # Simulate the pre-ADR-0015 state: the old mapping asserted 'automatic'.
    from carmanac.db.models import TransmissionType

    automatic = db.scalar(select(TransmissionType.id).where(TransmissionType.code == "automatic"))
    stale = FieldProvenance(
        configuration_id=config.id,
        field_name="transmission_type_id",
        observed_value=str(automatic),
        source_id=epa_source.id,
        raw_record_id=rec.id,
        scraped_at=rec.fetched_at,
    )
    db.add(stale)
    config.transmission_type_id = automatic
    db.commit()

    stats = run_epa_attach_pass(db)

    db.refresh(config)
    db.refresh(stale)
    assert stats.columns_corrected == 1
    assert config.transmission_type_id is None
    assert stale.superseded_by is not None and stale.superseded_by != stale.id
    successor = db.get(FieldProvenance, stale.superseded_by)
    assert successor.observed_value is None


def test_refresh_leaves_other_sources_columns_alone(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """A column another source asserts is reconciliation's contest, never
    this pass's overwrite."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(db, epa_source, 203, "Toyota", "4Runner", 2020, trany="Automatic (AM-S7)")
    run_epa_attach_pass(db)
    config = db.scalars(select(Configuration)).one()
    from carmanac.db.models import TransmissionType

    dct = db.scalar(select(TransmissionType.id).where(TransmissionType.code == "dct"))
    other = FieldProvenance(
        configuration_id=config.id,
        field_name="transmission_type_id",
        observed_value=str(dct),
        source_id=wikidata_source.id,
    )
    db.add(other)
    config.transmission_type_id = dct
    db.commit()

    stats = run_epa_attach_pass(db)

    db.refresh(config)
    assert stats.columns_corrected == 0
    assert config.transmission_type_id == dct


def test_aspiration_and_flex_fuel_land_as_eav(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    """tCharger -> aspiration=turbo, FFV -> flex_fuel, with row-level
    provenance; a twincharged row (both flags) asserts nothing."""
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(db, epa_source, 204, "Toyota", "4Runner", 2020, tCharger="T", atvType="FFV")
    _land_vehicle(db, epa_source, 205, "Toyota", "4Runner", 2021, tCharger="T", sCharger="S")

    stats = run_epa_attach_pass(db)

    assert stats.eav_written == 2  # aspiration + flex_fuel for 2020; nothing for 2021
    from carmanac.db.models import AttributeDefinition

    keys = {i: k for i, k in db.execute(select(AttributeDefinition.id, AttributeDefinition.key))}
    rows = db.scalars(select(ConfigurationAttribute)).all()
    by_key = {keys[r.attribute_id]: r for r in rows}
    assert by_key["aspiration"].value_text == "turbo"
    assert by_key["flex_fuel"].value_boolean is True
    assert all(r.source_id == epa_source.id and r.raw_record_id is not None for r in rows)


def test_natural_gas_maps_to_cng(db, wikidata_source, vpic_source, epa_source):  # noqa: F811
    _toyota_4runner(db, wikidata_source, vpic_source)
    _land_vehicle(db, epa_source, 206, "Toyota", "4Runner", 1998, fuelType1="Natural Gas")

    run_epa_attach_pass(db)

    from carmanac.db.models import FuelType

    cng = db.scalar(select(FuelType.id).where(FuelType.code == "cng"))
    assert db.scalars(select(Configuration)).one().fuel_type_id == cng
