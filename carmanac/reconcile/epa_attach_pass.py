"""The EPA attach pass (ADR 0014 §3-§4): the first configurations.

Two bridges, both laddered, never fuzzy. Per current `vehicle:<id>` record:

    make bridge   - normalized EPA make -> vPIC make name -> company
                    (99.0% measured), else the curated EPA_MAKE_MATCHES
                    registry, else the row waits with one flag per distinct
                    make string.
    model ladder  - 1. exact: normalized model = an as-filed model name
                    2. baseModel: normalized baseModel = an as-filed name
                    3. trim-parse: the longest as-filed name that prefixes
                       the model string ON A WORD BOUNDARY (the Ranger /
                       Range Rover lesson); the residue is the trim
                    4. residue: one flag per distinct (make, model) string,
                       with difflib candidates under that company

An attached row resolves its `model_year` period - (model, year), created
if vPIC's list lacks it, since EPA asserts exactly the same shape - and
lands in one configuration per natural key (period, trim, US market,
drivetrain, body NULL). Several EPA rows legitimately share one
configuration (an automatic and a manual of the same trim); columns are
written only where every contributing row agrees, and `vehicle:<id>`
external ids are written only for 1:1 groups (ADR 0011 §4). Facts carry
field-level provenance rows (ADR 0002) from a representative record; the
decision log maps every EPA row to its configuration either way.

`generation_id` stays NULL on every configuration this pass writes -
placement is evidence this pass does not carry (ADR 0014 §1-2).

Batch-committed (ADR 0014 §5). Re-runs converge: occupancy caches make
every group a match instead of a duplicate.
"""

from __future__ import annotations

import contextlib
import difflib
import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import (
    AttributeDefinition,
    CataloguePeriod,
    Company,
    Configuration,
    ConfigurationAttribute,
    ExternalId,
    FieldProvenance,
    MatchDecision,
    Model,
    PeriodKind,
    RawRecord,
    ReconciliationFlag,
)
from carmanac.ingest.epa.bulk import EPA_SOURCE_NAME
from carmanac.ingest.landing import get_source
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.engine import _current_records, _supersede
from carmanac.reconcile.matching import normalize_name

log = logging.getLogger(__name__)

PASS_NAME = "epa_attach"
_PREFIX = "vehicle:"
_COMMIT_CHUNK = 250  # groups per commit
_DECISION_CHUNK = 500

US_MARKET_CODE = "US"

# EPA `drive` -> drivetrains.code. Ambiguous strings ("2-Wheel Drive",
# "4-Wheel or All-Wheel Drive") map to nothing rather than a guess.
DRIVE_MAP: dict[str, str] = {
    "Front-Wheel Drive": "fwd",
    "Rear-Wheel Drive": "rwd",
    "All-Wheel Drive": "awd",
    "4-Wheel Drive": "4wd",
    "Part-time 4-Wheel Drive": "4wd",
}

# EPA `atvType` first (it distinguishes electrified kinds), then fuelType1.
ATV_FUEL_MAP: dict[str, str] = {
    "EV": "bev",
    "Plug-in Hybrid": "phev",
    "Hybrid": "hev",
}
FUEL1_MAP: dict[str, str] = {
    "Regular Gasoline": "gasoline",
    "Midgrade Gasoline": "gasoline",
    "Premium Gasoline": "gasoline",
    "Diesel": "diesel",
    "Electricity": "bev",
    "Natural Gas": "cng",
    "Hydrogen": "hydrogen",
}

# The spec columns this pass writes and refreshes (ADR 0014 §4, ADR 0015).
SPEC_COLUMNS: tuple[str, ...] = (
    "engine_displacement_cc",
    "cylinders",
    "fuel_type_id",
    "transmission_type_id",
    "mpg_city",
    "mpg_highway",
    "mpg_combined",
    "mpge_combined",
    "electric_range_km",
)

# The EAV keys this pass writes (registered in attribute_definitions by
# migration ea565b7ea489 before any landing - the charter rule).
EAV_KEYS: tuple[str, ...] = ("aspiration", "flex_fuel")

_MILES_TO_KM = 1.609344


@dataclass
class EpaAttachStats:
    """What one EPA attach pass did."""

    processed: int = 0
    attached_exact: int = 0
    attached_base: int = 0
    attached_prefix: int = 0
    waits_unbridged_make: int = 0
    no_model_match: int = 0
    periods_created: int = 0
    configurations_created: int = 0
    configurations_existing: int = 0
    external_ids_written: int = 0
    column_conflicts: int = 0
    columns_corrected: int = 0
    eav_written: int = 0
    flags_opened: int = 0

    def summary(self) -> str:
        attached = self.attached_exact + self.attached_base + self.attached_prefix
        return (
            f"processed={self.processed} attached={attached} "
            f"(exact={self.attached_exact} base={self.attached_base} "
            f"prefix={self.attached_prefix}) | "
            f"waits_unbridged_make={self.waits_unbridged_make} "
            f"no_model_match={self.no_model_match} | "
            f"periods_created={self.periods_created} "
            f"configs: created={self.configurations_created} "
            f"existing={self.configurations_existing} "
            f"external_ids={self.external_ids_written} "
            f"column_conflicts={self.column_conflicts} "
            f"columns_corrected={self.columns_corrected} "
            f"eav_written={self.eav_written} flags={self.flags_opened}"
        )


@dataclass
class _Group:
    """All EPA rows resolving to one configuration natural key."""

    model_id: int
    year: int
    trim: str | None
    drivetrain_id: int | None
    members: list[tuple[RawRecord, dict, dict]] = field(default_factory=list)


def _tokens(s: str) -> list[str]:
    """Whitespace tokens; normalization of their prefix-joins matches
    normalize_name of the corresponding substring (both strip non-alnum)."""
    return s.split()


class _EpaAttachPass:
    """One run of the EPA attach. Holds the per-run caches; `run()` is the
    entry point. Deliberately not reusable across runs."""

    def __init__(self, session: Session):
        self.session = session
        self.stats = EpaAttachStats()
        self.source = get_source(session, EPA_SOURCE_NAME)
        self.vpic_source = get_source(session, VPIC_SOURCE_NAME)
        self.decisions: list[dict] = []

        kind = session.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
        if kind is None:  # pragma: no cover - migration-seeded lookup
            raise RuntimeError("period_kinds seed row 'model_year' missing")
        self.model_year_kind_id: int = kind.id

        from carmanac.db.models import Drivetrain, MarketRegion

        market = session.scalar(select(MarketRegion).where(MarketRegion.code == US_MARKET_CODE))
        if market is None:  # pragma: no cover - migration-seeded lookup
            raise RuntimeError("market_regions seed row 'US' missing")
        self.us_market_id: int = market.id
        self.drivetrain_by_code: dict[str, int] = {
            code: dt_id for dt_id, code in session.execute(select(Drivetrain.id, Drivetrain.code))
        }
        from carmanac.db.models import FuelType, TransmissionType

        self.fuel_by_code: dict[str, int] = {
            code: ft_id for ft_id, code in session.execute(select(FuelType.id, FuelType.code))
        }
        self.trans_by_code: dict[str, int] = {
            code: tt_id
            for tt_id, code in session.execute(select(TransmissionType.id, TransmissionType.code))
        }

        # The make bridge: normalized vPIC make name -> matched company id.
        self.company_by_make_norm: dict[str, int] = {}
        make_external = {
            external_id: company_id
            for external_id, company_id in session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.source_id == self.vpic_source.id,
                    ExternalId.external_id.like("make:%"),
                    ExternalId.company_id.isnot(None),
                )
            )
        }
        for record in _current_records(session, self.vpic_source.id):
            if not record.external_id.startswith("make:"):
                continue
            company_id = make_external.get(record.external_id)
            name = record.payload.get("make_name")
            if company_id is not None and name:
                self.company_by_make_norm[normalize_name(name)] = company_id

        # The registry hop: normalized EPA make -> QID -> company.
        qid_to_company: dict[str, int] = {
            qid: company_id
            for qid, company_id in session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.company_id.isnot(None),
                    ExternalId.external_id.like("Q%"),
                )
            )
        }
        self.registry_company: dict[str, int] = {}
        for make_norm, qid in policy.EPA_MAKE_MATCHES.items():
            company_id = qid_to_company.get(policy.IDENTITY_MERGES.get(qid, qid))
            if company_id is not None:
                self.registry_company[make_norm] = company_id

        # As-filed models per company: normalized name -> model id, plus the
        # company/model rows for slugs and candidates.
        self.models: dict[int, Model] = {m.id: m for m in session.scalars(select(Model))}
        self.companies: dict[int, Company] = {c.id: c for c in session.scalars(select(Company))}
        self.models_by_company: dict[int, dict[str, int]] = defaultdict(dict)
        for m in self.models.values():
            self.models_by_company[m.company_id][normalize_name(m.name)] = m.id

        # Occupancy caches - what makes re-runs converge.
        self.period_by_key: dict[tuple[int, int], int] = {
            (model_id, start): pid
            for pid, model_id, start in session.execute(
                select(
                    CataloguePeriod.id, CataloguePeriod.model_id, CataloguePeriod.start_year
                ).where(CataloguePeriod.period_kind_id == self.model_year_kind_id)
            )
        }
        self.config_by_key: dict[tuple, int] = {}
        self.config_slugs: set[str] = set()
        for cfg_id, period_id, trim, market_id, dt_id, body_id, slug in session.execute(
            select(
                Configuration.id,
                Configuration.catalogue_period_id,
                Configuration.trim_name,
                Configuration.market_region_id,
                Configuration.drivetrain_id,
                Configuration.body_style_id,
                Configuration.slug,
            )
        ):
            self.config_by_key[(period_id, trim, market_id, dt_id, body_id)] = cfg_id
            self.config_slugs.add(slug)

        self.vehicle_external: set[str] = set(
            session.scalars(
                select(ExternalId.external_id).where(
                    ExternalId.source_id == self.source.id,
                    ExternalId.external_id.like(_PREFIX + "%"),
                )
            )
        )

        # Open flags, keyed by the question they represent - one flag per
        # distinct string, never per row.
        self.open_questions: set[tuple[str, str, str]] = set()
        for flag in session.scalars(
            select(ReconciliationFlag).where(
                ReconciliationFlag.kind == "match_review",
                ReconciliationFlag.status == "open",
                ReconciliationFlag.source_id == self.source.id,
            )
        ):
            d = flag.detail or {}
            self.open_questions.add((d.get("reason", ""), d.get("make", ""), d.get("model", "")))

        # Sole-source refresh caches (ADR 0015 §1): this pass may correct a
        # column it alone asserts; a column any OTHER source asserts is
        # reconciliation's job, not this pass's.
        self.config_cols: dict[int, dict] = {}
        for row in session.execute(
            select(Configuration.id, *[getattr(Configuration, c) for c in SPEC_COLUMNS])
        ):
            self.config_cols[row[0]] = dict(zip(SPEC_COLUMNS, row[1:], strict=True))
        self.epa_live: dict[tuple[int, str], int] = {}
        self.other_live: set[tuple[int, str]] = set()
        for fp_id, cfg_id, fname, src in session.execute(
            select(
                FieldProvenance.id,
                FieldProvenance.configuration_id,
                FieldProvenance.field_name,
                FieldProvenance.source_id,
            ).where(
                FieldProvenance.configuration_id.isnot(None),
                FieldProvenance.superseded_by.is_(None),
            )
        ):
            if src == self.source.id:
                self.epa_live[(cfg_id, fname)] = fp_id
            else:
                self.other_live.add((cfg_id, fname))
        self.attr_ids: dict[str, int] = {
            key: attr_id
            for key, attr_id in session.execute(
                select(AttributeDefinition.key, AttributeDefinition.id).where(
                    AttributeDefinition.key.in_(EAV_KEYS)
                )
            )
        }
        missing = set(EAV_KEYS) - set(self.attr_ids)
        if missing:  # pragma: no cover - migration-seeded registry
            raise RuntimeError(f"attribute_definitions missing keys: {sorted(missing)}")
        # (config, attr) -> (row id, current value) for live EPA-written EAV.
        self.eav_live: dict[tuple[int, int], tuple[int, object]] = {}
        for ca_id, cfg_id, attr_id, vt, vb in session.execute(
            select(
                ConfigurationAttribute.id,
                ConfigurationAttribute.configuration_id,
                ConfigurationAttribute.attribute_id,
                ConfigurationAttribute.value_text,
                ConfigurationAttribute.value_boolean,
            ).where(
                ConfigurationAttribute.attribute_id.in_(list(self.attr_ids.values())),
                ConfigurationAttribute.superseded_by.is_(None),
                ConfigurationAttribute.source_id == self.source.id,
            )
        ):
            self.eav_live[(cfg_id, attr_id)] = (ca_id, vt if vt is not None else vb)

    # --- resolution ----------------------------------------------------------

    def _bridge_make(self, make: str) -> int | None:
        norm = normalize_name(make)
        return self.company_by_make_norm.get(norm) or self.registry_company.get(norm)

    def _resolve_model(
        self, company_id: int, model_str: str, base_model: str
    ) -> tuple[int, str | None, str, str] | None:
        """(model_id, trim, method, rung) or None. Trim is the word-boundary
        residue of the model string past the as-filed name; a baseModel hit
        whose name does not prefix the string keeps the whole string as trim."""
        our = self.models_by_company.get(company_id, {})
        n_model = normalize_name(model_str)
        if n_model in our:
            return our[n_model], None, "exact_model", "1"

        tokens = _tokens(model_str)
        joins = {normalize_name(" ".join(tokens[:k])): k for k in range(1, len(tokens))}

        n_base = normalize_name(base_model) if base_model else ""
        if n_base and n_base in our:
            k = joins.get(n_base)
            trim = " ".join(tokens[k:]) if k is not None else model_str
            return our[n_base], trim or None, "base_model", "2"

        # Longest as-filed name that is a whole-token prefix of the string.
        best: tuple[int, int] | None = None  # (k, model_id)
        for norm, k in joins.items():
            model_id = our.get(norm)
            if model_id is not None and (best is None or k > best[0]):
                best = (k, model_id)
        if best is not None:
            k, model_id = best
            return model_id, " ".join(tokens[k:]) or None, "model_prefix", "3"
        return None

    def _specs(self, payload: dict) -> tuple[dict, dict]:
        """(spec columns, EAV facts) one EPA row asserts."""
        specs: dict = {}
        displ = payload.get("displ")
        if displ:
            with contextlib.suppress(ValueError):
                specs["engine_displacement_cc"] = round(float(displ) * 1000)
        cyl = payload.get("cylinders")
        if cyl:
            with contextlib.suppress(ValueError):
                specs["cylinders"] = int(float(cyl))
        fuel_code = ATV_FUEL_MAP.get(payload.get("atvType") or "") or FUEL1_MAP.get(
            payload.get("fuelType1") or ""
        )
        if fuel_code:
            specs["fuel_type_id"] = self.fuel_by_code.get(fuel_code)
        trany = payload.get("trany") or ""
        if trany:
            # ADR 0015 §1: only what EPA is unambiguous
            # about. AV codes are CVTs with simulated ratios; AM/AM-S are
            # automated manuals whose single-vs-dual-clutch difference EPA
            # cannot see - they assert NOTHING, no bucket word.
            if "variable gear" in trany.lower() or trany.startswith("Automatic (AV"):
                code = "cvt"
            elif trany.startswith("Automatic (AM"):
                code = None
            elif trany.startswith("Manual"):
                code = "manual"
            elif trany.startswith("Automatic"):
                code = "automatic"
            else:
                code = None
            if code:
                specs["transmission_type_id"] = self.trans_by_code.get(code)

        def _num(key: str) -> float | None:
            v = payload.get(key)
            try:
                n = float(v)
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None

        electric = (payload.get("atvType") == "EV") or (payload.get("fuelType1") == "Electricity")
        comb, city, hwy = _num("comb08"), _num("city08"), _num("highway08")
        if electric:
            if comb:
                specs["mpge_combined"] = comb
        else:
            if city:
                specs["mpg_city"] = city
            if hwy:
                specs["mpg_highway"] = hwy
            if comb:
                specs["mpg_combined"] = comb
        rng = _num("range")
        if rng:
            specs["electric_range_km"] = round(rng * _MILES_TO_KM)

        eav: dict = {}
        turbo = (payload.get("tCharger") or "").strip()
        sup = (payload.get("sCharger") or "").strip()
        if turbo and sup:
            pass  # twincharged: EPA's two booleans cannot rank them - assert nothing
        elif turbo:
            eav["aspiration"] = "turbo"
        elif sup:
            eav["aspiration"] = "supercharged"
        if payload.get("atvType") == "FFV" or "FFV" in (payload.get("eng_dscr") or ""):
            eav["flex_fuel"] = True
        return specs, eav

    # --- bookkeeping ---------------------------------------------------------

    def _decide(
        self,
        record: RawRecord,
        rung: str | None,
        method: str | None,
        outcome: str,
        detail: dict | None = None,
    ) -> None:
        self.decisions.append(
            {
                "source_id": self.source.id,
                "pass_name": PASS_NAME,
                "external_id": record.external_id,
                "raw_record_id": record.id,
                "rung": rung,
                "method": method,
                "outcome": outcome,
                "detail": detail,
                "reconciler_version": policy.RECONCILER_VERSION,
            }
        )

    def _flush_decisions(self) -> None:
        for start in range(0, len(self.decisions), _DECISION_CHUNK):
            chunk = self.decisions[start : start + _DECISION_CHUNK]
            stmt = pg_insert(MatchDecision).values(chunk)
            self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_match_decisions_source_id_pass_name_external_id",
                    set_={
                        "raw_record_id": stmt.excluded.raw_record_id,
                        "rung": stmt.excluded.rung,
                        "method": stmt.excluded.method,
                        "outcome": stmt.excluded.outcome,
                        "detail": stmt.excluded.detail,
                        "reconciler_version": stmt.excluded.reconciler_version,
                        "decided_at": func.now(),
                    },
                )
            )

    def _flag(self, record: RawRecord, reason: str, make: str, model: str, detail: dict) -> None:
        key = (reason, make, model)
        if key in self.open_questions:
            return
        self.session.add(
            ReconciliationFlag(
                kind="match_review",
                raw_record_id=record.id,
                source_id=self.source.id,
                detail={"reason": reason, "make": make, "model": model, **detail},
            )
        )
        self.open_questions.add(key)
        self.stats.flags_opened += 1

    # --- materialization -----------------------------------------------------

    def _period_id(self, model_id: int, year: int) -> int:
        pid = self.period_by_key.get((model_id, year))
        if pid is None:
            period = CataloguePeriod(
                model_id=model_id,
                period_kind_id=self.model_year_kind_id,
                start_year=year,
                end_year=year,
            )
            self.session.add(period)
            self.session.flush()
            pid = period.id
            self.period_by_key[(model_id, year)] = pid
            self.stats.periods_created += 1
        return pid

    def _slug(self, group: _Group) -> str:
        model = self.models[group.model_id]
        company = self.companies[model.company_id]
        parts = [company.slug, model.slug, str(group.year)]
        if group.trim:
            trim_slug = "-".join(
                "".join(ch for ch in tok.casefold() if ch.isalnum()) for tok in group.trim.split()
            ).strip("-")
            if trim_slug:
                parts.append(trim_slug)
        code = next(
            (c for c, i in self.drivetrain_by_code.items() if i == group.drivetrain_id), None
        )
        if code:
            parts.append(code)
        base = "-".join(p for p in parts if p)
        slug, n = base, 1
        while slug in self.config_slugs:
            n += 1
            slug = f"{base}-{n}"
        return slug

    def _materialize(self, group: _Group) -> None:
        period_id = self._period_id(group.model_id, group.year)
        key = (period_id, group.trim, self.us_market_id, group.drivetrain_id, None)
        cfg_id = self.config_by_key.get(key)
        created = cfg_id is None

        # Column/EAV agreement across the group's rows: unanimous or nothing.
        agreed: dict = {}
        for col in SPEC_COLUMNS:
            values = {specs[col] for _, specs, _ in group.members if specs.get(col) is not None}
            if len(values) == 1:
                agreed[col] = values.pop()
            elif len(values) > 1:
                self.stats.column_conflicts += 1
        agreed_eav: dict = {}
        for key in EAV_KEYS:
            values = {eav[key] for _, _, eav in group.members if eav.get(key) is not None}
            if len(values) == 1:
                agreed_eav[key] = values.pop()
            elif len(values) > 1:
                self.stats.column_conflicts += 1

        rep = min(group.members, key=lambda m: m[0].id)[0]
        if created:
            config = Configuration(
                catalogue_period_id=period_id,
                market_region_id=self.us_market_id,
                trim_name=group.trim,
                drivetrain_id=group.drivetrain_id,
                slug=self._slug(group),
                **agreed,
            )
            self.session.add(config)
            self.session.flush()
            cfg_id = config.id
            self.config_by_key[key] = cfg_id
            self.config_slugs.add(config.slug)
            self.stats.configurations_created += 1
            self.config_cols[cfg_id] = {c: agreed.get(c) for c in SPEC_COLUMNS}
            # Field-level provenance (ADR 0002) from a representative record:
            # the group's first row by EPA id. Every member row's linkage is
            # in the decision log regardless.
            for col, value in agreed.items():
                fp = FieldProvenance(
                    configuration_id=cfg_id,
                    field_name=col,
                    observed_value=str(value),
                    source_id=self.source.id,
                    raw_record_id=rep.id,
                    scraped_at=rep.fetched_at,
                )
                self.session.add(fp)
            self.session.flush()
            for key, value in agreed_eav.items():
                self._write_eav(cfg_id, key, value, rep)
        else:
            self.stats.configurations_existing += 1
            self._refresh_columns(cfg_id, agreed, rep)
            for key, value in agreed_eav.items():
                current = self.eav_live.get((cfg_id, self.attr_ids[key]))
                if current is None:
                    self._write_eav(cfg_id, key, value, rep)
                elif current[1] != value:
                    self._correct_eav(cfg_id, key, value, rep)

        if len(group.members) == 1:
            record = group.members[0][0]
            if record.external_id not in self.vehicle_external:
                self.session.add(
                    ExternalId(
                        configuration_id=cfg_id,
                        source_id=self.source.id,
                        external_id=record.external_id,
                    )
                )
                self.vehicle_external.add(record.external_id)
                self.stats.external_ids_written += 1

    @staticmethod
    def _same(a, b) -> bool:
        if a is None or b is None:
            return a is None and b is None
        try:
            return float(a) == float(b)
        except (TypeError, ValueError):
            return a == b

    def _refresh_columns(self, cfg_id: int, agreed: dict, rep: RawRecord) -> None:
        """Sole-source column refresh (ADR 0015 §1): where EPA is the only
        live asserter of a column and its recomputed answer changed - a
        mapping correction, a re-landed CSV - the old assertion is
        superseded and the column follows. A column any other source
        asserts is left alone; that contest is reconciliation's."""
        current = self.config_cols.get(cfg_id, {})
        config = None
        for col in SPEC_COLUMNS:
            new = agreed.get(col)
            if self._same(current.get(col), new):
                continue
            if (cfg_id, col) in self.other_live:
                continue
            if config is None:
                config = self.session.get(Configuration, cfg_id)
            new_values = {
                "configuration_id": cfg_id,
                "field_name": col,
                "observed_value": None if new is None else str(new),
                "source_id": self.source.id,
                "raw_record_id": rep.id,
                "scraped_at": rep.fetched_at,
            }
            old_id = self.epa_live.get((cfg_id, col))
            if old_id is not None:
                successor = _supersede(
                    self.session, self.session.get(FieldProvenance, old_id), new_values
                )
            else:
                successor = FieldProvenance(**new_values)
                self.session.add(successor)
                self.session.flush()
            self.epa_live[(cfg_id, col)] = successor.id
            setattr(config, col, new)
            current[col] = new
            self.stats.columns_corrected += 1

    def _write_eav(self, cfg_id: int, key: str, value, rep: RawRecord) -> None:
        attr_id = self.attr_ids[key]
        row = ConfigurationAttribute(
            configuration_id=cfg_id,
            attribute_id=attr_id,
            value_text=value if isinstance(value, str) else None,
            value_boolean=value if isinstance(value, bool) else None,
            source_id=self.source.id,
            raw_record_id=rep.id,
            scraped_at=rep.fetched_at,
        )
        self.session.add(row)
        self.session.flush()
        self.eav_live[(cfg_id, attr_id)] = (row.id, value)
        self.stats.eav_written += 1

    def _correct_eav(self, cfg_id: int, key: str, value, rep: RawRecord) -> None:
        """The three-step dance, EAV edition (same live-slot uniqueness)."""
        attr_id = self.attr_ids[key]
        old_id, _ = self.eav_live[(cfg_id, attr_id)]
        old = self.session.get(ConfigurationAttribute, old_id)
        old.superseded_by = old.id
        self.session.flush()
        self._write_eav(cfg_id, key, value, rep)
        old.superseded_by = self.eav_live[(cfg_id, attr_id)][0]
        self.session.flush()

    # --- run -----------------------------------------------------------------

    def run(self) -> EpaAttachStats:
        records = [
            r
            for r in _current_records(self.session, self.source.id)
            if r.external_id.startswith(_PREFIX)
        ]
        log.info(
            "EPA attach pass: %d vehicle records (reconciler v%s)",
            len(records),
            policy.RECONCILER_VERSION,
        )

        groups: dict[tuple, _Group] = {}
        unbridged: dict[str, int] = defaultdict(int)
        method_stat = {
            "exact_model": "attached_exact",
            "base_model": "attached_base",
            "model_prefix": "attached_prefix",
        }
        for record in records:
            self.stats.processed += 1
            p = record.payload
            make, model_str = (p.get("make") or "").strip(), (p.get("model") or "").strip()
            company_id = self._bridge_make(make)
            if company_id is None:
                self.stats.waits_unbridged_make += 1
                unbridged[make] += 1
                self._flag(record, "unbridged_make", make, "", {})
                self._decide(record, None, None, "waits_unbridged_make", {"make": make})
                continue

            base = (p.get("baseModel") or "").strip()
            resolved = self._resolve_model(company_id, model_str, base)
            if resolved is None:
                self.stats.no_model_match += 1
                our_names = [
                    self.models[mid].name for mid in self.models_by_company[company_id].values()
                ]
                candidates = difflib.get_close_matches(model_str, our_names, n=5, cutoff=0.6)
                self._flag(
                    record,
                    "no_model_match",
                    make,
                    model_str,
                    {"baseModel": p.get("baseModel"), "candidates": candidates},
                )
                self._decide(record, None, None, "flagged_no_model_match", {"model": model_str})
                continue

            model_id, trim, method, rung = resolved
            setattr(self.stats, method_stat[method], getattr(self.stats, method_stat[method]) + 1)
            try:
                year = int(p.get("year"))
            except (TypeError, ValueError):
                self._decide(
                    record, rung, method, "flagged_no_model_match", {"bad_year": p.get("year")}
                )
                continue
            drive_id = self.drivetrain_by_code.get(DRIVE_MAP.get(p.get("drive") or "", ""))
            gkey = (model_id, year, trim, drive_id)
            if gkey not in groups:
                groups[gkey] = _Group(model_id, year, trim, drive_id)
            specs, eav = self._specs(p)
            groups[gkey].members.append((record, specs, eav))
            self._decide(
                record,
                rung,
                method,
                "attached",
                {"model": self.models[model_id].slug, "year": year, "trim": trim},
            )

        since_commit = 0
        for gkey in sorted(groups, key=lambda k: (k[0], k[1], k[2] or "", k[3] or 0)):
            self._materialize(groups[gkey])
            since_commit += 1
            if since_commit >= _COMMIT_CHUNK:
                self.session.commit()
                since_commit = 0

        self._flush_decisions()
        self.session.commit()
        if unbridged:
            top = sorted(unbridged.items(), key=lambda t: -t[1])[:10]
            log.info("unbridged makes (rows): %s", top)
        log.info("EPA attach pass done: %s", self.stats.summary())
        return self.stats


def run_epa_attach_pass(session: Session) -> EpaAttachStats:
    return _EpaAttachPass(session).run()
