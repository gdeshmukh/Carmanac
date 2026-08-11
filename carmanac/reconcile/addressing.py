"""Addresses: where a page's name is composed, and the only place.

A slug is the public address of a page. Nothing joins on it, no registry keys
on it, and no row fails to exist because its address is taken - identity is
the natural key and `external_ids`. That makes an address a **projection**,
like any other value the reconciler derives: recomputed from current data on
every run, converging to a no-op when nothing moved.

Which is why there is no rename script. Changing how addresses look means
editing the grammar here and re-running `recompute_addresses`; a company
whose name is arbitrated re-addresses its cars on the next run, because the
address was never a promise. That trade flips the day pages are published -
then addresses freeze and retired ones need forwarding - and that belongs to
the read surface's own decision, not here.

Collisions never guess. Two companies that compose the same address are a
recorded human judgment waiting to happen (`COMPANY_SLUG_OVERRIDES`), and
until it lands the losing row simply has no address.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    Drivetrain,
    ExternalId,
    Generation,
    GenerationModelLink,
    Model,
)
from carmanac.reconcile import policy
from carmanac.reconcile.names import normalize_name
from carmanac.reconcile.sources.wikidata_models import strip_prefix
from carmanac.reconcile.sources.wikipedia_sections import ORDINAL_WORDS

# The dash family folds to a hyphen BEFORE the ASCII strip: NFKD cannot
# decompose an en dash, so "Renault-Nissan" written with one used to lose its
# word boundary entirely (`renaultnissan...`).
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−", "-"))

# A year range anywhere, not just leading: the Cadillac species wears it
# mid-slug (`de-ville-1961-64`), the Impreza section heading wears it in
# front. Two-digit end years are the common form.
_YEAR_RANGE = re.compile(r"(?:^|-)\d{4}-\d{2,4}(?:-|$)")
_NUMERAL_ORDINAL = re.compile(r"-\d+(?:st|nd|rd|th)-generation$")
_SECTION_ORDINAL = re.compile(r"^section:.+#(?P<ordinal>\d+)$")
_NAME_ORDINAL = re.compile(r"\b(?P<n>\d+)(?:st|nd|rd|th) generation\b", re.I)


def slugify(name: str) -> str:
    """ASCII-folded, lowercased, hyphenated. Empty when a name has no ASCII
    form at all (fully CJK): the caller flags for a curated romanization
    rather than falling back to a source's identifier, which would put one
    source's ID scheme into a public address."""
    folded = (
        unicodedata.normalize("NFKD", name.translate(_DASHES))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def nonconforming_slug(slug: str) -> str | None:
    """The drift classes an address may never wear, or None if it is clean.

    Each one is a source artifact that reached a public address: production
    time (a fact, not identity), a wiki category page's title, and the
    numeral form of an ordinal the grammar spells out.
    """
    if not slug:
        return "no_ascii_name"
    if _YEAR_RANGE.search(slug):
        return "year_range"
    if slug.startswith("category-"):
        return "source_artifact_title"
    if _NUMERAL_ORDINAL.search(slug):
        return "numeral_ordinal"
    return None


def strip_marque(nameplate: str, prefixes: tuple[str, ...]) -> str:
    """Drop the marque from a nameplate for display under its own company -
    "Mercedes-AMG GT" is `amg-gt` under Mercedes-Benz.

    Stripping stops when nothing but digits would survive: `Mazda3` is a
    nameplate, not a 3, and `mazda3-bk` beats `3-bk` as an address.
    """
    for prefix in prefixes:
        stripped = strip_prefix(nameplate, prefix, normalize_name)
        if stripped != nameplate and any(ch.isalpha() for ch in stripped):
            return stripped
    return nameplate


def generation_display(nameplate: str, chassis_codes: list[str] | None, ordinal: int | None) -> str:
    """`<nameplate> (<codes>)` when codes are known, `<nameplate> (<ordinal>
    generation)` otherwise. Empty when neither is on record - a generation
    whose only distinguishing fact is its production span has no name the
    grammar can compose, and gets no address until one arrives."""
    if chassis_codes:
        return f"{nameplate} ({'/'.join(chassis_codes)})"
    if ordinal and 1 <= ordinal <= len(ORDINAL_WORDS):
        return f"{nameplate} ({ORDINAL_WORDS[ordinal - 1]} generation)"
    return ""


def configuration_slug(
    company_slug: str | None,
    model_slug: str | None,
    year: int,
    trim: str | None,
    drivetrain_code: str | None,
) -> str | None:
    """company · model · year · [trim] · [drivetrain], every token through
    the same slugify. None when a parent has no address to build on."""
    if not company_slug or not model_slug:
        return None
    parts = [company_slug, model_slug, str(year)]
    if trim:
        trim_slug = slugify(trim)
        if trim_slug:
            parts.append(trim_slug)
    if drivetrain_code:
        parts.append(drivetrain_code)
    return "-".join(parts)


@dataclass
class AddressStats:
    companies_addressed: int = 0
    generations_addressed: int = 0
    configurations_addressed: int = 0
    contested: int = 0

    def summary(self) -> str:
        return (
            f"re-addressed: companies={self.companies_addressed} "
            f"generations={self.generations_addressed} "
            f"configurations={self.configurations_addressed} "
            f"| contested (no address): {self.contested}"
        )


def _company_evidence(session: Session) -> dict[int, tuple[int, int]]:
    """How much of a claim each company has on a contested address, most
    significant first: whether a filing authority names it, then how many
    nameplates it holds.

    Evidence, not arrival order. Two companies named Toyota is not a
    disambiguation problem when one of them has 54 nameplates and a vPIC
    filing and the other has two Wikidata statements; making both wait for a
    curated pin would leave the real one unreachable in the meantime.
    """
    filed = {
        cid
        for (cid,) in session.execute(
            select(ExternalId.company_id).where(ExternalId.external_id.like("make:%"))
        )
    }
    nameplates = dict(
        session.execute(
            select(Model.company_id, func.count(Model.id)).group_by(Model.company_id)
        ).all()
    )
    return {
        c.id: (1 if c.id in filed else 0, nameplates.get(c.id, 0))
        for c in session.scalars(select(Company))
    }


def recompute_addresses(session: Session) -> AddressStats:
    """Re-derive every address from current data. Idempotent by construction:
    a second run changes nothing unless the data changed."""
    stats = AddressStats()
    qid_by_company: dict[int, set[str]] = {}
    for company_id, external_id in session.execute(
        select(ExternalId.company_id, ExternalId.external_id).where(
            ExternalId.company_id.isnot(None), ExternalId.external_id.regexp_match("^Q[0-9]+$")
        )
    ):
        qid_by_company.setdefault(company_id, set()).add(external_id)

    # --- companies ---------------------------------------------------------
    evidence = _company_evidence(session)
    wanted: dict[str, list[int]] = {}
    pinned: dict[int, str] = {}
    for company in session.scalars(select(Company)):
        pin = next(
            (
                policy.COMPANY_SLUG_OVERRIDES[q]
                for q in sorted(qid_by_company.get(company.id, ()))
                if q in policy.COMPANY_SLUG_OVERRIDES
            ),
            None,
        )
        if pin:
            pinned[company.id] = pin
            continue
        slug = slugify(company.name)
        if slug and slug not in policy.RESERVED_COMPANY_SLUGS:
            wanted.setdefault(slug, []).append(company.id)

    company_slug: dict[int, str | None] = dict.fromkeys(evidence, None)
    company_slug.update(pinned)
    pinned_slugs = set(pinned.values())
    holder = {c.id: c.slug for c in session.scalars(select(Company))}
    for slug, claimants in wanted.items():
        if slug in pinned_slugs:
            continue  # a pin already holds it; every mechanical claimant waits
        # Evidence first; whoever already answers at this address breaks the
        # tie. Between two equally evidence-less namesakes there is no honest
        # ranking, and moving the incumbent would be churn for nothing.
        winner = max(claimants, key=lambda cid: (*evidence[cid], holder[cid] == slug))
        company_slug[winner] = slug
        stats.contested += len(claimants) - 1

    for company in session.scalars(select(Company)):
        target = company_slug.get(company.id)
        if company.slug != target:
            company.slug = target
            stats.companies_addressed += 1

    # --- generations -------------------------------------------------------
    prefixes = _strip_prefixes(session)
    models = {m.id: m for m in session.scalars(select(Model))}
    ordinals: dict[int, int] = {}
    demoted: set[int] = set()
    for generation_id, external_id in session.execute(
        select(ExternalId.generation_id, ExternalId.external_id).where(
            ExternalId.generation_id.isnot(None)
        )
    ):
        match = _SECTION_ORDINAL.match(external_id)
        if match:
            ordinals[generation_id] = int(match.group("ordinal"))
        if external_id in policy.NOT_A_GENERATION:
            demoted.add(generation_id)
    linked: dict[int, set[int]] = {}
    for generation_id, model_id in session.execute(
        select(GenerationModelLink.generation_id, GenerationModelLink.model_id).where(
            GenerationModelLink.superseded_by.is_(None)
        )
    ):
        linked.setdefault(generation_id, set()).add(model_id)

    taken: dict[tuple[int, str], int] = {}
    for generation in sorted(session.scalars(select(Generation)), key=lambda g: g.id):
        target = None
        model_ids = linked.get(generation.id, set())
        # One linked model names a generation; zero or many cannot, and a row
        # ruled not-a-generation keeps whatever it has until it is re-kinded.
        if len(model_ids) == 1 and generation.id not in demoted:
            nameplate = strip_marque(
                models[next(iter(model_ids))].name, prefixes.get(generation.company_id, ())
            )
            ordinal = ordinals.get(generation.id)
            if ordinal is None and generation.name:
                # The ordinal is a fact the display name carries; reading it
                # back is not the same as reading the slug, which is only an
                # address and may already be wrong.
                in_name = _NAME_ORDINAL.search(generation.name)
                ordinal = int(in_name.group("n")) if in_name else None
            display = generation_display(nameplate, generation.chassis_codes, ordinal)
            candidate = slugify(display) if display else ""
            if candidate and not nonconforming_slug(candidate):
                key = (generation.company_id, candidate)
                if taken.get(key, generation.id) == generation.id:
                    taken[key] = generation.id
                    target = candidate
        if target is None and generation.slug and not nonconforming_slug(generation.slug):
            # Nothing composable, but what it wears is clean: keep it rather
            # than un-addressing a page over missing links.
            target = generation.slug
            taken[(generation.company_id, target)] = generation.id
        if generation.slug != target:
            generation.slug = target
            stats.generations_addressed += 1
    session.flush()

    # --- configurations ----------------------------------------------------
    drivetrains = dict(session.execute(select(Drivetrain.id, Drivetrain.code)).all())
    company_by_id = {c.id: c for c in session.scalars(select(Company))}
    seen: dict[str, int] = {}
    rows = session.execute(
        select(
            Configuration.id,
            Configuration.slug,
            Configuration.trim_name,
            Configuration.drivetrain_id,
            CataloguePeriod.start_year,
            Model.slug,
            Model.company_id,
        )
        .join(CataloguePeriod, Configuration.catalogue_period_id == CataloguePeriod.id)
        .join(Model, CataloguePeriod.model_id == Model.id)
        .order_by(Configuration.id)
    ).all()
    updates: list[tuple[int, str | None]] = []
    for cfg_id, slug, trim, dt_id, year, model_slug, cid in rows:
        target = configuration_slug(
            company_by_id[cid].slug, model_slug, year, trim, drivetrains.get(dt_id)
        )
        if target is not None and seen.setdefault(target, cfg_id) != cfg_id:
            # Two cars composing one address means the natural key split rows
            # a source considers the same car (`E350 4Matic` twice, cased two
            # ways). That is an entity-resolution question; neither row gets
            # the address until it is answered.
            target = None
            stats.contested += 1
        if slug != target:
            updates.append((cfg_id, target))
    for cfg_id, target in updates:
        session.get(Configuration, cfg_id).slug = target
    stats.configurations_addressed = len(updates)
    session.flush()
    return stats


def _strip_prefixes(session: Session) -> dict[int, tuple[str, ...]]:
    """Every name form a company wears (its own, plus its vPIC make names),
    longest first - ADR 0013 §1's stripping input."""
    norms: dict[int, set[str]] = {
        cid: {normalize_name(name)}
        for cid, name in session.execute(select(Company.id, Company.name))
    }
    for company_id, make_name in session.execute(
        text(
            """SELECT DISTINCT ei.company_id, rr.payload->>'make_name'
               FROM external_ids ei
               JOIN sources s ON s.id = ei.source_id AND s.name = 'NHTSA vPIC'
               JOIN raw_scrape.raw_records rr
                 ON rr.source_id = s.id AND rr.external_id = ei.external_id
               WHERE ei.external_id LIKE 'make:%' AND ei.company_id IS NOT NULL"""
        )
    ):
        if make_name:
            norms.setdefault(company_id, set()).add(normalize_name(make_name))
    return {cid: tuple(sorted(ns, key=len, reverse=True)) for cid, ns in norms.items()}
