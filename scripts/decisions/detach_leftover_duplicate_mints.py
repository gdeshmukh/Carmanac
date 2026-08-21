"""Detach unruled QIDs that minted at duplicate-ruled addresses.

Before the pass held half-ruled groups contested, registering some of a duplicate
group's members shrank the group past the contest check, and a lone unruled
leftover minted at the base address. The pass now flags that leftover; this
detaches the QIDs the hole already let through, leaving each model row bare
for the eras to attach under. Occupants that label-MATCHED their model are
the nameplate arrangement the rulings assumed and are not touched - only
`registry_mint` attachments qualify.

Dry-run by default; `--execute` applies. The detached QID re-contests on the
next wd-models run.
"""

from __future__ import annotations

import sys

from sqlalchemy import select, text

from carmanac.db.models import Company, ExternalId, Model, Source
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy


def census(session) -> list[tuple[str, Model, str]]:
    """(qid, model, company slug) per unruled occupant of a ruled base."""
    ruled_bases = set()
    for target in policy.WIKIDATA_DUPLICATE_NAMEPLATES.values():
        company_slug, _, slug = target.partition(":")[2].partition("/")
        ruled_bases.add((company_slug, slug))
    source_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
    out = []
    for qid, model, company_slug in session.execute(
        select(ExternalId.external_id, Model, Company.slug)
        .join(Model, Model.id == ExternalId.model_id)
        .join(Company, Company.id == Model.company_id)
        .where(ExternalId.source_id == source_id)
    ):
        if (company_slug, model.slug) not in ruled_bases:
            continue
        if qid in policy.WIKIDATA_DUPLICATE_NAMEPLATES:
            continue
        method = session.scalar(
            text(
                "SELECT method FROM match_decisions WHERE external_id = :qid"
                " ORDER BY id DESC LIMIT 1"
            ),
            {"qid": qid},
        )
        if method == "registry_mint":
            out.append((qid, model, company_slug))
    return out


def main() -> None:
    execute = "--execute" in sys.argv
    with SessionLocal() as session:
        hits = census(session)
        for qid, model, company_slug in hits:
            print(f"{qid}  {company_slug}/{model.slug}  (model id {model.id})")
        if not hits:
            print("no leftover mints found")
            return
        if not execute:
            print(f"\ndry run: {len(hits)} occupant(s); pass --execute to detach")
            return
        source_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
        for qid, model, _company_slug in hits:
            session.execute(
                text(
                    "DELETE FROM external_ids WHERE external_id = :qid"
                    " AND source_id = :sid AND model_id = :mid"
                ),
                {"qid": qid, "sid": source_id, "mid": model.id},
            )
            # The mint's own assertions (name, summary); the bare row keeps
            # its name column as the address's display name.
            session.execute(
                text("DELETE FROM field_provenance WHERE model_id = :mid AND source_id = :sid"),
                {"mid": model.id, "sid": source_id},
            )
            model.summary = None
        session.commit()
        print(f"\ndetached {len(hits)} occupant(s); re-run the wd-models pass")


if __name__ == "__main__":
    main()
