"""The five-level entity hierarchy - the spine of the schema.

    companies -> models -> generations -> catalogue_periods -> configurations

These are IDENTITY tables (ADR 0002). They hold identity plus their descriptive
and spec columns as the *current best value*. They carry no row-level
provenance and no `superseded_by`: a make either exists or it does not, and
"which source told us BMW was founded in 1916" is a field-level fact recorded in
`field_provenance`, not a property of the identity row. Entities are upserted by
natural key. External identifiers (Wikidata QID, NHTSA id, ...) live in
`external_ids` (ADR 0003), not as columns here.

Every spec-bearing row still foreign-keys back toward `configurations`, because
a configuration (period x trim x market x drivetrain) is the only level at
which a single spec value is unambiguous.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from carmanac.db.base import Base, ProvenanceMixin, TimestampMixin, provenance_table_args
from carmanac.db.models.lookups import PeriodKind


class Company(Base, TimestampMixin):
    """Any organisation that appears on or behind a vehicle (ADR 0006).

    BMW, Pontiac, Alpina, Singer, Zagato, Murphy - all one table. "Make" is a
    *role* a company holds (see `CompanyRole`), not a separate kind of entity:
    Alpina holds its own WMI and also builds on BMW hardware, and modelling that
    as two tables would give one company two rows and two pages.

    Defunct marques (Pontiac, Saab, Plymouth) stay top-level and simply carry a
    `defunct_year`. Modeling corporate parents is deferred to its own ADR - see
    PROGRESS.md Open Questions.
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("countries.id"), index=True)
    founded_year: Mapped[int | None] = mapped_column(SmallInteger)
    defunct_year: Mapped[int | None] = mapped_column(SmallInteger)  # null = still active

    # Prose. `summary` is the one-line blurb every entity carries (Wikidata's
    # description maps straight onto it); `description` is the longer narrative
    # a company homepage needs. Both are reconciled facts like any other column,
    # so which source wrote them is recorded in `field_provenance`.
    summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)

    # Reconciled fact like the prose columns (ADR 0007 §8): Wikidata already
    # returns it, entity pages want it, and discarding it at reconcile time
    # was the only alternative.
    website: Mapped[str | None] = mapped_column(Text)

    models: Mapped[list[Model]] = relationship(back_populates="company")

    # Trigram index: entity resolution matches incoming source names against
    # these fuzzily ("BMW AG" -> "BMW"), so a plain btree is not enough.
    __table_args__ = (
        Index(
            "idx_companies_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class CompanyRoleAssignment(Base, ProvenanceMixin):
    """One source's assertion that a company holds a role (ADR 0007 §8, F7).

    A PER-SOURCE ASSERTION STORE, not one-row-per-fact: "BMW is a
    manufacturer" per Wikidata and per vPIC are two live rows. The company
    holds the role if ANY live row says so (EXISTS at read time);
    corroboration is visible as multiple live rows; a source withdrawing the
    claim is supersession, not deletion. One row per fact was the same defect
    ADR 0002 fixed for entity columns - a second source had nowhere to land,
    which structurally blocked vPIC arbitration of Wikidata-asserted roles.

    `manufacturer` should agree with the presence of a WMI in `external_ids`
    (ADR 0005's test). Disagreement is a reconciliation flag, not a silent
    overwrite.
    """

    __tablename__ = "company_role_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    company_role_id: Mapped[int] = mapped_column(
        ForeignKey("company_roles.id"), nullable=False, index=True
    )
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("company_role_assignments.id", name="fk_company_role_assignments_superseded_by"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        *provenance_table_args(),
        # One LIVE assertion per (fact, source) - field_provenance's shape.
        # NULLS NOT DISTINCT so seed rows without a source count as one
        # anonymous asserter rather than never colliding.
        Index(
            "uq_company_role_assignment_live",
            "company_id",
            "company_role_id",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )


class Model(Base, TimestampMixin):
    """A manufacturer's leaf catalogue designation, as filed (ADR 0011):
    'Corolla', '330i', '911', 'C-Class'. Granularity is the manufacturer's
    own - an engine badge, a body split, and a nameplate all sit at this
    level. Series/lines ('3 Series') are aggregations over models, not rows
    here."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Points at `companies` regardless of role (ADR 0006), so Singer's product
    # lines get the same catalogue depth as BMW's with no exclusive arc.
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="models")
    generations: Mapped[list[Generation]] = relationship(back_populates="model")

    __table_args__ = (
        # Slug is unique *within* a company, so two companies may both have a
        # '3-series' without colliding.
        UniqueConstraint("company_id", "slug", name="uq_models_company_id_slug"),
        Index(
            "idx_models_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class ModelLine(Base, TimestampMixin):
    """A grouping row, NOT an entity in the hierarchy (ADR 0012 §4).

    "3 Series" is an aggregation over as-filed models (ADR 0011 §2), served
    as a browse view over its members - /makes/bmw/3-series is a page over
    memberships, never a sixth level. Nothing in the FK spine points here,
    and lines hold NO external ids: the series QID stays on the raw record,
    and identity resolves by the (company, slug) natural key. A source
    entity mapping to a line therefore never violates ADR 0011 §4's 1:1
    external-id rule.
    """

    __tablename__ = "model_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    company: Mapped[Company] = relationship()
    members: Mapped[list[ModelLineMember]] = relationship(back_populates="line")

    __table_args__ = (
        UniqueConstraint("company_id", "slug", name="uq_model_lines_company_id_slug"),
    )


class ModelLineMember(Base, ProvenanceMixin):
    """One source's assertion that a model belongs to a line (ADR 0012 §4).

    A fact like any other - per-source assertion store with supersession,
    the `company_role_assignments` shape: membership holds if ANY live row
    says so, a second source corroborating is a second live row, and a
    source withdrawing the claim is supersession, never deletion. Uniqueness
    is per (line, model, source) live rows only - deliberately never across
    sources.
    """

    __tablename__ = "model_line_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_line_id: Mapped[int] = mapped_column(
        ForeignKey("model_lines.id"), nullable=False, index=True
    )
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), nullable=False, index=True)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("model_line_members.id", name="fk_model_line_members_superseded_by"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    line: Mapped[ModelLine] = relationship(back_populates="members")
    model: Mapped[Model] = relationship()

    __table_args__ = (
        *provenance_table_args(),
        # One LIVE membership assertion per (line, model, source) - the
        # company_role_assignments shape. NULLS NOT DISTINCT so sourceless
        # rows count as one anonymous asserter rather than never colliding.
        Index(
            "uq_model_line_members_live",
            "model_line_id",
            "model_id",
            "source_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )


class Generation(Base, TimestampMixin):
    """A generation of a model - E46, G80, XV70.

    This is the level enthusiasts actually search by, which is why it carries
    `chassis_codes`.
    """

    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    # One generation routinely spans several codes (E46 sedan/coupe/touring
    # carry different internal codes), hence an array rather than a scalar.
    chassis_codes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    start_year: Mapped[int | None] = mapped_column(SmallInteger)
    end_year: Mapped[int | None] = mapped_column(SmallInteger)  # null = still in production
    summary: Mapped[str | None] = mapped_column(Text)

    model: Mapped[Model] = relationship(back_populates="generations")
    # Placed configurations (ADR 0014: placement is configuration-level,
    # evidence-gated). Generation pages render over these plus the
    # chassis-code views.
    configurations: Mapped[list[Configuration]] = relationship(back_populates="generation")

    __table_args__ = (
        UniqueConstraint("model_id", "slug", name="uq_generations_model_id_slug"),
        # GIN on the array: "show me every E46" is the search this column exists
        # for, and `text[] @> ARRAY['E46']` cannot use a btree.
        Index("idx_generations_chassis_codes", "chassis_codes", postgresql_using="gin"),
        # Generations and trims are where entity resolution actually gets hard
        # ("E46 330Ci", "330i Sport"), so they need the same fuzzy matching
        # `companies` and `models` already have.
        Index(
            "idx_generations_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class CataloguePeriod(Base, TimestampMixin):
    """The 4th level (ADR 0009, re-parented by ADR 0014): the span a source
    catalogues a MODEL under. Pure time, thin by design.

    `kind` says which convention the row follows: `model_year` (US-style,
    start = end - the shape vPIC/EPA assert), `production_period` (Euro/JDM
    "built 1998-2005"), or `phase` (zenki/kouki, Phase 1/2). `end_year` NULL
    means still in production. A row exists only because a current raw record
    asserted that exact shape - no pass may fabricate per-year rows from a
    period or vice versa. One model routinely carries both kinds at once
    (US years next to a Euro period); queries by year use containment.
    Same-kind overlap within a model is a reconciliation flag, never a
    constraint - two sources bracketing a run differently must both land.

    Periods hang under models, not generations (ADR 0014): a period records
    exactly what the year-asserting sources say - (model, span) - and
    generation placement is an evidence-gated fact on the CONFIGURATION,
    because one model year can contain configurations of two generations at
    once (the 2019 AMG GT: C190 coupes and X290 4-doors side by side).
    """

    __tablename__ = "catalogue_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("models.id"), nullable=False, index=True)
    period_kind_id: Mapped[int] = mapped_column(
        ForeignKey("period_kinds.id"), nullable=False, index=True
    )
    start_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_year: Mapped[int | None] = mapped_column(SmallInteger)  # null = still in production

    model: Mapped[Model] = relationship()
    period_kind: Mapped[PeriodKind] = relationship()
    configurations: Mapped[list[Configuration]] = relationship(back_populates="catalogue_period")

    __table_args__ = (
        # NULLS NOT DISTINCT: an open-ended period (end_year NULL) must still
        # collide with its duplicate.
        UniqueConstraint(
            "model_id",
            "period_kind_id",
            "start_year",
            "end_year",
            name="uq_catalogue_periods_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("end_year IS NULL OR end_year >= start_year", name="end_not_before_start"),
    )


class Configuration(Base, TimestampMixin):
    """The atomic unit: catalogue period x trim x market x drivetrain.

    Core spec columns live here as the current best value. A spec earns a column
    only if >=80% of configurations would plausibly have a value (CLAUDE.md);
    everything sparser goes to EAV via `configuration_attributes`. Which source
    each column's value came from is recorded per field in `field_provenance`
    (ADR 0002); the column itself is the reconciled winner.

    Units are metric and explicit in the column name (_cc, _kg, _mm, _nm, _km)
    to remove ambiguity at ingest time. mpg/mpge stay imperial because they are
    EPA-defined metrics, not raw measurements.
    """

    __tablename__ = "configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    catalogue_period_id: Mapped[int] = mapped_column(
        ForeignKey("catalogue_periods.id"), nullable=False, index=True
    )
    # NOT NULL, unlike the other lookups. Market is part of the definition of a
    # configuration ("year x trim x market x drivetrain"), and a 2004 M3 is a
    # genuinely different car in the US and Europe - different output, its own
    # page. Nullable would also break the natural key below, because NULLs do
    # not collide in Postgres: two "unknown market" rows for the same car would
    # never conflict. Unknown markets use the explicit GLOBAL / UNKNOWN rows.
    market_region_id: Mapped[int] = mapped_column(
        ForeignKey("market_regions.id"), nullable=False, index=True
    )
    # Generation placement (ADR 0014): NULLABLE and evidence-gated - written
    # only when a source places THIS car (body style, chassis code, dated
    # spans), never inferred from the year alone. NULL means "no source has
    # placed this car yet", visibly; the model -> year pages render it fully
    # either way. This is the goal level of the goal-per-car hierarchy.
    generation_id: Mapped[int | None] = mapped_column(ForeignKey("generations.id"), index=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)

    # --- identity / classification (mostly NHTSA vPIC) ---
    trim_name: Mapped[str | None] = mapped_column(Text)  # 'M340i', 'LE', 'GTI Autobahn'
    body_style_id: Mapped[int | None] = mapped_column(ForeignKey("body_styles.id"), index=True)
    drivetrain_id: Mapped[int | None] = mapped_column(ForeignKey("drivetrains.id"), index=True)
    doors: Mapped[int | None] = mapped_column(SmallInteger)
    seating_capacity: Mapped[int | None] = mapped_column(SmallInteger)

    # --- powertrain summary ---
    # Denormalized for fast list/compare queries. The authoritative powertrain
    # detail lives in engines/transmissions via the join tables; these columns
    # are reconciled against those, never treated as the primary record.
    fuel_type_id: Mapped[int | None] = mapped_column(ForeignKey("fuel_types.id"), index=True)
    engine_displacement_cc: Mapped[int | None] = mapped_column(Integer)
    cylinders: Mapped[int | None] = mapped_column(SmallInteger)
    transmission_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("transmission_types.id"), index=True
    )

    # --- performance / economy (mostly EPA fueleconomy.gov) ---
    power_hp: Mapped[int | None] = mapped_column(Integer)
    torque_nm: Mapped[int | None] = mapped_column(Integer)
    mpg_city: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpg_highway: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpg_combined: Mapped[float | None] = mapped_column(Numeric(5, 1))
    mpge_combined: Mapped[float | None] = mapped_column(Numeric(5, 1))  # electrified
    electric_range_km: Mapped[int | None] = mapped_column(Integer)  # BEV/PHEV

    # --- physical (mostly Wikidata) ---
    curb_weight_kg: Mapped[int | None] = mapped_column(Integer)
    length_mm: Mapped[int | None] = mapped_column(Integer)
    width_mm: Mapped[int | None] = mapped_column(Integer)
    height_mm: Mapped[int | None] = mapped_column(Integer)
    wheelbase_mm: Mapped[int | None] = mapped_column(Integer)

    # --- launch pricing (MSRP-at-launch only is in scope) ---
    msrp_launch_amount: Mapped[float | None] = mapped_column(Numeric(12, 2))
    msrp_launch_currency_id: Mapped[int | None] = mapped_column(
        ForeignKey("currencies.id"), index=True
    )

    summary: Mapped[str | None] = mapped_column(Text)

    catalogue_period: Mapped[CataloguePeriod] = relationship(back_populates="configurations")
    generation: Mapped[Generation | None] = relationship(back_populates="configurations")

    __table_args__ = (
        # THE NATURAL KEY. Slug is derived from a name, so it cannot carry
        # identity: two sources wording the same car differently produce
        # different slugs and both insert. These five columns are what actually
        # makes a configuration distinct, and are what the reconciler matches on
        # when deciding whether an incoming record is a car we already have.
        #
        # NULLS NOT DISTINCT (Postgres 15+) is load-bearing. By default NULLs
        # never collide, so two rows with an unknown trim or body style would
        # both insert and the constraint would quietly do nothing for exactly
        # the sparse records that need it most. This makes "unknown" compare
        # equal to "unknown", without forcing a fake UNKNOWN lookup row onto
        # every dimension.
        UniqueConstraint(
            "catalogue_period_id",
            "trim_name",
            "market_region_id",
            "drivetrain_id",
            "body_style_id",
            name="uq_configurations_natural_key",
            postgresql_nulls_not_distinct=True,
        ),
        # Slug is now a display artifact, but the public route is a flat
        # /configurations/<slug> lookup, so it still needs to resolve without
        # knowing the model year first.
        UniqueConstraint("slug", name="uq_configurations_slug"),
        Index(
            "idx_configurations_trim_name_trgm",
            "trim_name",
            postgresql_using="gin",
            postgresql_ops={"trim_name": "gin_trgm_ops"},
        ),
    )
