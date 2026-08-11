"""The QID-suffix company rename batch (ADR 0019 §2, §5).

140 companies wear source-leaking `-q<digits>` slugs - the class the
2026-07-29 ruling condemned - and ~97 namesake clusters hold their bare slug
by arrival order. This script computes the whole batch from the ADR's rules
and applies it behind the standing dry-run gate:

- **suffix drop**: a suffixed company whose base no other company claims (by
  slug, name, or vPIC make name) renames to the bare base. A claimed base
  joins the hold-list instead - merges outrank renames (the AUDI twin).
- **cluster pin**: every member of a contested base cluster - suffixed or
  bare-holding - renames to its `COMPANY_SLUG_OVERRIDES` pin. The script
  refuses to execute while any member lacks one.
- **bare vacate**: a contested base belongs to nobody. The demotion's
  trigger-written alias is pruned in the same transaction (window-open,
  never served - ADR 0004's own-artifacts clause) and the base must already
  sit in `RESERVED_COMPANY_SLUGS`, which occupies it forever.
- **leaf recompute**: configuration slugs are mint-time snapshots, and this
  batch is the one window-open exception - every stored slug that differs
  from the v16 composition (renamed parents, the 38 double-hyphen
  malformations, hardened slugify) renames with a trigger-written alias.

Every rename records its old address mechanically (migration e8f2a91c7d30);
the script also refuses to execute while any slug-pair registry entry would
go stale - the same-commit rewrite is forced, not conventional.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    Drivetrain,
    ExternalId,
    Model,
    SlugAlias,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import hold_address_lock
from carmanac.reconcile.engine import slugify

_QID_SUFFIX = re.compile(r"^(?P<base>.+)-(?P<qid>q\d+)$")

# Rulings from dry-run review: company id -> reason. Held companies keep
# their slug this batch; the reason prints so the hold survives re-reads.
HOLDS: dict[str, str] = {}


def _company_qids(session: Session) -> dict[int, set[str]]:
    qids: dict[int, set[str]] = defaultdict(set)
    for company_id, external_id in session.execute(
        select(ExternalId.company_id, ExternalId.external_id).where(
            ExternalId.company_id.isnot(None), ExternalId.external_id.like("Q%")
        )
    ):
        qids[company_id].add(external_id)
    return qids


def _name_claims(session: Session) -> dict[str, set[int]]:
    """base slug -> companies whose recorded names slugify onto it (their own
    name, plus vPIC make names - the AUDI form that catches the twin)."""
    claims: dict[str, set[int]] = defaultdict(set)
    for company_id, name in session.execute(select(Company.id, Company.name)):
        claims[slugify(name)].add(company_id)
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
            claims[slugify(make_name)].add(company_id)
    return claims


def plan(session: Session) -> dict:
    companies = {c.id: c for c in session.scalars(select(Company))}
    qids = _company_qids(session)
    name_claims = _name_claims(session)

    # base -> members (suffixed by their base, bare rows by their whole slug).
    base_members: dict[str, list[int]] = defaultdict(list)
    suffixed: dict[int, str] = {}
    for cid, c in companies.items():
        m = _QID_SUFFIX.match(c.slug)
        if m and m.group("qid").upper() in qids.get(cid, set()):
            suffixed[cid] = m.group("base")
            base_members[m.group("base")].append(cid)
        else:
            base_members[c.slug].append(cid)

    renames: dict[int, tuple[str, str]] = {}  # id -> (new_slug, action)
    holds: list[tuple[int, str]] = []
    vacated: list[str] = []
    missing_pins: list[int] = []

    for cid, base in suffixed.items():
        members = base_members[base]
        claimants = name_claims.get(base, set()) - {cid}
        if str(cid) in HOLDS:
            holds.append((cid, HOLDS[str(cid)]))
        elif len(members) == 1 and not claimants:
            renames[cid] = (base, "suffix_drop")
        elif len(members) == 1:
            names = ", ".join(sorted(companies[x].name for x in claimants))
            holds.append((cid, f"base {base!r} claimed by name: {names}"))
        else:
            pin = _pin(cid, qids)
            if pin is None:
                missing_pins.append(cid)
            else:
                renames[cid] = (pin, "cluster_pin")

    # Bare holders inside contested bases lose the bare slug. A cluster with
    # a held member does not vacate - the base cannot be reserved while a
    # member still legitimately wears it.
    for base, members in base_members.items():
        if len(members) < 2:
            continue
        if any(str(cid) in HOLDS for cid in members):
            holds.append((members[0], f"cluster {base!r} has a held member; base stays live"))
            continue
        for cid in members:
            if cid in suffixed:
                continue
            pin = _pin(cid, qids)
            if pin is None:
                missing_pins.append(cid)
            else:
                renames[cid] = (pin, "bare_vacate")
        vacated.append(base)

    return {
        "companies": companies,
        "renames": renames,
        "holds": holds,
        "vacated": vacated,
        "missing_pins": missing_pins,
    }


def _pin(cid: int, qids: dict[int, set[str]]) -> str | None:
    pins = {
        policy.COMPANY_SLUG_OVERRIDES[q]
        for q in qids.get(cid, set())
        if q in policy.COMPANY_SLUG_OVERRIDES
    }
    if len(pins) > 1:
        raise RuntimeError(f"company {cid} holds conflicting pins: {sorted(pins)}")
    return pins.pop() if pins else None


def leaf_recompute(session: Session, renames: dict[int, tuple[str, str]]) -> dict:
    """Stored configuration slug vs the v16 composition, post-rename world.
    Mirrors _EpaAttachPass._slug; test_epa_attach_pass pins the shape."""
    company_slug = {
        c.id: renames[c.id][0] if c.id in renames else c.slug
        for c in session.scalars(select(Company))
    }
    dt_code = dict(session.execute(select(Drivetrain.id, Drivetrain.code)).all())
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
    ).all()
    live = {slug for _, slug, *_ in rows}
    changes: dict[int, tuple[str, str]] = {}
    collisions: list[tuple[str, str]] = []
    for cfg_id, slug, trim, dt_id, year, model_slug, cid in rows:
        parts = [company_slug[cid], model_slug, str(year)]
        if trim:
            trim_slug = slugify(trim)
            if trim_slug:
                parts.append(trim_slug)
        code = dt_code.get(dt_id)
        if code:
            parts.append(code)
        target = "-".join(parts)
        if target == slug:
            continue
        if target in live:
            collisions.append((slug, target))
            continue
        live.add(target)
        changes[cfg_id] = (slug, target)
    return {"changes": changes, "collisions": collisions}


def registry_preflight(session: Session, renames: dict[int, tuple[str, str]]) -> list[str]:
    """Every slug-pair registry entry must resolve in the POST-rename world,
    or the rewrite has not landed and executing would strand it."""
    new_slug = {cid: slug for cid, (slug, _) in renames.items()}
    live = {
        f"{new_slug.get(cid, co_slug)}/{m_slug}"
        for co_slug, m_slug, cid in session.execute(
            select(Company.slug, Model.slug, Company.id).join(Model, Model.company_id == Company.id)
        )
    }
    stale = [
        f"{name} {key!r} -> {pair!r}"
        for name, entries in (
            ("WIKIDATA_MODEL_MATCHES", policy.WIKIDATA_MODEL_MATCHES.items()),
            ("WIKIDATA_MODEL_NEGATIVES", policy.WIKIDATA_MODEL_NEGATIVES),
            ("SECTION_ARTICLE_MODELS", policy.SECTION_ARTICLE_MODELS.items()),
        )
        for key, pair in entries
        if pair not in live
    ]
    return stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        p = plan(session)
        leaves = leaf_recompute(session, p["renames"])
        stale = registry_preflight(session, p["renames"])

        by_action: dict[str, list[str]] = defaultdict(list)
        for cid, (new, action) in sorted(p["renames"].items(), key=lambda kv: kv[1][0]):
            by_action[action].append(
                f"{p['companies'][cid].slug} -> {new}  ({p['companies'][cid].name})"
            )
        for action in ("suffix_drop", "cluster_pin", "bare_vacate"):
            rows = by_action.get(action, [])
            print(f"\n{action} ({len(rows)}):")
            for row in rows:
                print(f"  {row}")
        print(f"\nholds ({len(p['holds'])}):")
        for cid, reason in p["holds"]:
            print(f"  {p['companies'][cid].slug}: {reason}")
        print(f"\nvacated bases -> RESERVED_COMPANY_SLUGS ({len(p['vacated'])}):")
        for base in sorted(p["vacated"]):
            marker = "" if base in policy.RESERVED_COMPANY_SLUGS else "  !! NOT IN RESERVED SET"
            print(f"  {base}{marker}")
        if p["missing_pins"]:
            n = len(p["missing_pins"])
            print(f"\nmissing pins ({n}) - each needs a COMPANY_SLUG_OVERRIDES judgment:")
            for cid in p["missing_pins"]:
                c = p["companies"][cid]
                qs = ", ".join(sorted(_company_qids(session).get(cid, set())))
                print(f"  {c.slug}  ({c.name}; {qs})")
        print(f"\nleaf recomputes ({len(leaves['changes'])}):")
        for old, new in sorted(leaves["changes"].values())[:20]:
            print(f"  {old} -> {new}")
        if len(leaves["changes"]) > 20:
            print(f"  ... and {len(leaves['changes']) - 20} more")
        for old, target in leaves["collisions"]:
            print(f"  !! kept (recompute collides): {old} -> {target}")
        if stale:
            print(f"\nregistry entries that would go stale ({len(stale)}):")
            for entry in stale:
                print(f"  !! {entry}")

        if not args.execute:
            print("\ndry run - review, land pins/reserved/registry edits, then --execute")
            return 0

        blockers = []
        if p["missing_pins"]:
            blockers.append(f"{len(p['missing_pins'])} cluster members lack pins")
        unreserved = [b for b in p["vacated"] if b not in policy.RESERVED_COMPANY_SLUGS]
        if unreserved:
            blockers.append(f"{len(unreserved)} vacated bases not in RESERVED_COMPANY_SLUGS")
        if stale:
            blockers.append(f"{len(stale)} registry entries would go stale")
        if blockers:
            print("\nREFUSED: " + "; ".join(blockers))
            return 1

        hold_address_lock(session)
        for cid, (new, _) in p["renames"].items():
            session.get(Company, cid).slug = new
        session.flush()
        # Window-open prune (ADR 0004 own-artifacts): the vacated bares were
        # never served; retaining their aliases would fossilize arrival order.
        pruned = 0
        for base in p["vacated"]:
            for alias in session.scalars(
                select(SlugAlias).where(SlugAlias.entity_kind == "company", SlugAlias.slug == base)
            ):
                session.delete(alias)
                pruned += 1
        for cfg_id, (_, target) in leaves["changes"].items():
            session.get(Configuration, cfg_id).slug = target
        session.commit()
        print(
            f"\nexecuted: {len(p['renames'])} company renames, "
            f"{len(leaves['changes'])} leaf renames, {pruned} bare aliases pruned"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
