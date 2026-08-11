"""The generation grammar batch (ADR 0019 §4).

Generation slugs drifted because two mint sites composed display names
differently and source headings leaked through (`2000-2007-subaru-impreza`,
`category-honda-hr-v-1st-generation`, numeral ordinals). v16's mint guards
stop new drift; this script renames the live nonconforming rows to the
decided grammar - `<stripped nameplate> (<codes>)` when chassis codes are
facts, `<stripped nameplate> (<ordinal-word> generation)` otherwise - as a
recompute diff, never a hand-picked list. Every rename records its old
address mechanically (migration e8f2a91c7d30).

Deliberately untouched: the conforming majority (bare codes and name+code
are both lawful realizations - the rule is one, the output follows source
naming), the four `NOT_A_GENERATION` rows (they await re-kinding; forcing
generation grammar onto rows ruled not-generations is churn), and anything
whose target is uncomputable or collides - those print and wait.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    ExternalId,
    Generation,
    GenerationModelLink,
    Model,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import alias_addresses, hold_address_lock
from carmanac.reconcile.engine import nonconforming_slug, slugify
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.sources.wikidata_models import strip_prefix
from carmanac.reconcile.sources.wikipedia_sections import ORDINAL_WORDS

_NUMERAL_ORDINAL = re.compile(r"-(?P<n>\d+)(?:st|nd|rd|th)-generation$")
_SECTION_KEY = re.compile(r"^section:.+#(?P<ordinal>\d+)$")


def _strip_prefixes(session: Session) -> dict[int, tuple[str, ...]]:
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


def plan(session: Session) -> dict:
    generations = list(session.scalars(select(Generation)))
    prefixes = _strip_prefixes(session)
    models = {m.id: m for m in session.scalars(select(Model))}

    demoted: set[int] = set(
        session.scalars(
            select(ExternalId.generation_id).where(
                ExternalId.generation_id.isnot(None),
                ExternalId.external_id.in_(policy.NOT_A_GENERATION),
            )
        )
    )
    ordinals: dict[int, int] = {}
    for generation_id, external_id in session.execute(
        select(ExternalId.generation_id, ExternalId.external_id).where(
            ExternalId.generation_id.isnot(None), ExternalId.external_id.like("section:%")
        )
    ):
        m = _SECTION_KEY.match(external_id)
        if m:
            ordinals[generation_id] = int(m.group("ordinal"))
    linked_models: dict[int, set[int]] = defaultdict(set)
    for generation_id, model_id in session.execute(
        select(GenerationModelLink.generation_id, GenerationModelLink.model_id).where(
            GenerationModelLink.superseded_by.is_(None)
        )
    ):
        linked_models[generation_id].add(model_id)

    occupied: dict[tuple[int, str], int] = {(g.company_id, g.slug): g.id for g in generations}
    for key, target in alias_addresses(session, "generation").items():
        occupied.setdefault(key, target)

    renames: dict[int, tuple[str, str, str]] = {}  # id -> (old, new, reason)
    unresolved: list[tuple[Generation, str]] = []
    proposals: list[tuple[Generation, str]] = []

    for g in generations:
        numeral = _NUMERAL_ORDINAL.search(g.slug)
        # The mint guard's own test, so the class this batch sweeps and the
        # class the passes refuse can never drift apart.
        reason = nonconforming_slug(g.slug)
        if g.id in demoted:
            if reason:
                unresolved.append((g, f"{reason}; demoted (NOT_A_GENERATION) - awaits re-kinding"))
            continue

        model_ids = linked_models.get(g.id, set())
        if len(model_ids) != 1:
            if reason:
                unresolved.append((g, f"{reason}; {len(model_ids)} linked models"))
            continue
        model = models[next(iter(model_ids))]
        nameplate = model.name
        for prefix in prefixes.get(g.company_id, ()):
            stripped = strip_prefix(nameplate, prefix, normalize_name)
            if stripped != nameplate:
                nameplate = stripped
                break

        if g.chassis_codes:
            display = f"{nameplate} ({'/'.join(g.chassis_codes)})"
        elif g.id in ordinals and 1 <= ordinals[g.id] <= len(ORDINAL_WORDS):
            display = f"{nameplate} ({ORDINAL_WORDS[ordinals[g.id] - 1]} generation)"
        elif numeral and 1 <= int(numeral.group("n")) <= len(ORDINAL_WORDS):
            display = f"{nameplate} ({ORDINAL_WORDS[int(numeral.group('n')) - 1]} generation)"
        else:
            if reason:
                unresolved.append((g, f"{reason}; no codes and no ordinal on record"))
            continue

        target = slugify(display)
        if target == g.slug:
            continue
        still = nonconforming_slug(target)
        holder = occupied.get((g.company_id, target))
        if reason is None:
            # Conforming, but the grammar would compose it differently (a
            # display name arbitrated since mint, the company-name-embedding
            # class). Addresses do not move on drift alone - ruling first.
            proposals.append((g, f"grammar would compose {target!r}"))
            continue
        if still is not None:
            unresolved.append((g, f"{reason}; recompute still nonconforming: {target!r}"))
            continue
        if holder is not None and holder != g.id:
            unresolved.append((g, f"{reason}; target {target!r} taken by generation {holder}"))
            continue
        occupied[(g.company_id, target)] = g.id
        renames[g.id] = (g.slug, target, reason)

    return {
        "generations": {g.id: g for g in generations},
        "renames": renames,
        "unresolved": unresolved,
        "proposals": proposals,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        p = plan(session)
        print(f"renames ({len(p['renames'])}):")
        for _, (old, new, reason) in sorted(p["renames"].items(), key=lambda kv: kv[1][0]):
            print(f"  {old} -> {new}  [{reason}]")
        print(f"\nunresolved - print and wait ({len(p['unresolved'])}):")
        for g, why in p["unresolved"]:
            print(f"  {g.slug}: {why}")
        print(f"\nproposals - conforming, but the grammar differs ({len(p['proposals'])}):")
        for g, why in p["proposals"]:
            print(f"  {g.slug}: {why}")

        if not args.execute:
            print("\ndry run - review the list, then --execute")
            return 0

        hold_address_lock(session)
        for gid, (_, new, _) in p["renames"].items():
            session.get(Generation, gid).slug = new
        session.commit()
        print(f"\nexecuted: {len(p['renames'])} generation renames (aliases trigger-written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
