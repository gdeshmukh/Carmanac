"""Print the live project state a session starts from.

PROGRESS.md's head carries intent (what's in flight, what's next, what's
open); the NUMBERS it used to carry go stale the moment work happens. This
script is the mechanical half of the session-start check: run it, diff its
output against the head's claims, and any drift is visible in seconds
instead of a hand-run query per claim. Read-only.

Usage:  .venv/bin/python scripts/status.py
"""

from __future__ import annotations

import subprocess

from sqlalchemy import bindparam, text

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def main() -> int:
    print(f"branch   : {_git('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(f"last     : {_git('log', '--oneline', '-1')}")

    with SessionLocal() as s:
        head = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"alembic  : {head}")

        for label, sql in (
            ("companies", "SELECT count(*) FROM companies"),
            ("models", "SELECT count(*) FROM models"),
            ("model_lines", "SELECT count(*) FROM model_lines"),
            ("model_line_members", "SELECT count(*) FROM model_line_members"),
            ("generations", "SELECT count(*) FROM generations"),
            ("catalogue_periods", "SELECT count(*) FROM catalogue_periods"),
            ("configurations", "SELECT count(*) FROM configurations"),
            ("external_ids", "SELECT count(*) FROM external_ids"),
            ("assertions (field_provenance)", "SELECT count(*) FROM field_provenance"),
            ("match decisions", "SELECT count(*) FROM match_decisions"),
        ):
            print(f"{label:<30}: {s.execute(text(sql)).scalar()}")

        print("raw records by source/kind:")
        for name, kind, n in s.execute(
            text(
                """
                SELECT so.name,
                       CASE WHEN rr.external_id LIKE 'make:%' THEN 'makes'
                            WHEN rr.external_id LIKE 'model:%' THEN 'models'
                            WHEN rr.external_id LIKE 'modelyears:%' THEN 'model-years'
                            WHEN rr.external_id LIKE 'vehicle:%' THEN 'vehicles'
                            WHEN rr.external_id LIKE 'infobox:%' THEN 'infoboxes'
                            WHEN rr.external_id LIKE 'article:%' THEN 'articles'
                            WHEN rr.external_id LIKE 'section-main:%' THEN 'section-mains'
                            -- bare QIDs split by the landing-stamped sweep
                            -- marker (ADR 0012 §1), never by payload shape
                            WHEN rr.external_id LIKE 'Q%'
                                 AND rr.payload->>'sweep' = 'models' THEN 'model entities'
                            WHEN rr.external_id LIKE 'Q%' THEN 'entities'
                            ELSE 'other' END,
                       count(*)
                FROM raw_scrape.raw_records rr JOIN sources so ON so.id = rr.source_id
                GROUP BY 1, 2 ORDER BY 1, 2
                """
            )
        ):
            print(f"  {name:<22} {kind:<10}: {n}")

        print("open flags by kind:")
        # match_review is split by the flagged record's kind: the make queue
        # (unmatched vPIC makes) and the model queue (slug collisions) are
        # different questions with different resolutions, and one number
        # covering both hides whichever grew.
        for kind, n in s.execute(
            text(
                """
                SELECT f.kind || CASE WHEN f.kind <> 'match_review' THEN ''
                                      -- bare-QID records (the Wikidata sweeps)
                                      -- have no kind prefix to split on
                                      WHEN rr.external_id LIKE 'Q%'
                                      THEN ' (wd ' || coalesce(rr.payload->>'sweep', 'make') || ')'
                                      ELSE ' (' || split_part(rr.external_id, ':', 1) || ')' END,
                       count(*)
                FROM reconciliation_flags f
                LEFT JOIN raw_scrape.raw_records rr ON rr.id = f.raw_record_id
                WHERE f.status = 'open' GROUP BY 1 ORDER BY 1
                """
            )
        ):
            print(f"  {kind:<22}: {n}")

        matched, landed = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'NHTSA vPIC' AND ei.external_id LIKE 'make:%'),
                  (SELECT count(*) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'NHTSA vPIC' AND rr.external_id LIKE 'make:%')
                """
            )
        ).one()
        print(f"vPIC makes matched            : {matched}/{landed}")

        reconciled, landed_models = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'NHTSA vPIC' AND ei.external_id LIKE 'model:%'),
                  (SELECT count(*) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'NHTSA vPIC' AND rr.external_id LIKE 'model:%')
                """
            )
        ).one()
        print(f"vPIC models reconciled        : {reconciled}/{landed_models}")

        year_models, epa_attached, epa_rows = s.execute(
            text(
                """
                SELECT
                  (SELECT count(DISTINCT model_id) FROM catalogue_periods),
                  (SELECT count(*) FROM match_decisions
                   WHERE pass_name = 'epa_attach' AND outcome = 'attached'),
                  (SELECT count(*) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'EPA fueleconomy.gov'
                     AND rr.external_id LIKE 'vehicle:%')
                """
            )
        ).one()
        print(f"models with year spine        : {year_models}")
        print(f"EPA rows attached             : {epa_attached}/{epa_rows}")

        wd_models, wd_gens, wd_swept = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'Wikidata' AND ei.model_id IS NOT NULL),
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'Wikidata' AND ei.generation_id IS NOT NULL),
                  (SELECT count(DISTINCT rr.external_id) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'Wikidata' AND rr.payload->>'sweep' = 'models')
                """
            )
        ).one()
        print(f"Wikidata model QIDs -> models : {wd_models}/{wd_swept} swept")
        print(f"Wikidata QIDs -> generations  : {wd_gens}")

        section_gens = s.execute(
            text(
                """
                SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                WHERE so.name = 'Wikipedia (English)' AND ei.external_id LIKE 'section:%'
                """
            )
        ).scalar()
        print(f"section-born generations      : {section_gens}")

        # The coverage funnel (ADR 0018): each stage is cumulative, so the
        # last line's denominator is visible all the way up. Curated article
        # routings (SECTION_ARTICLE_MODELS) count as reach - the AMG GT has
        # articles and placements without a 1:1 model QID.
        routed = sorted({pair for pair in policy.SECTION_ARTICLE_MODELS.values()}) or ["-/-"]
        routed_qids = sorted(policy.SECTION_ARTICLE_MODELS) or ["Q0"]
        funnel = s.execute(
            text(
                """
                WITH routed_models AS (
                  SELECT m.id FROM models m JOIN companies co ON co.id = m.company_id
                  WHERE co.slug || '/' || m.slug IN :routed
                ),
                stages AS (
                  SELECT m.id,
                    EXISTS (SELECT 1 FROM catalogue_periods p
                            JOIN configurations c ON c.catalogue_period_id = p.id
                            WHERE p.model_id = m.id) AS has_configs,
                    (EXISTS (SELECT 1 FROM external_ids ei
                             JOIN sources so ON so.id = ei.source_id
                             WHERE so.name = 'Wikidata' AND ei.model_id = m.id)
                     OR m.id IN (SELECT id FROM routed_models)) AS has_qid,
                    (EXISTS (SELECT 1 FROM external_ids ei
                             JOIN sources so ON so.id = ei.source_id
                             JOIN raw_scrape.raw_records rr
                               ON rr.external_id = 'article:' || ei.external_id
                             JOIN sources ws ON ws.id = rr.source_id
                              AND ws.name = 'Wikipedia (English)'
                             WHERE so.name = 'Wikidata' AND ei.model_id = m.id)
                     OR (m.id IN (SELECT id FROM routed_models)
                         AND EXISTS (SELECT 1 FROM raw_scrape.raw_records rr
                                     JOIN sources ws ON ws.id = rr.source_id
                                      AND ws.name = 'Wikipedia (English)'
                                     WHERE substring(rr.external_id FROM 9)
                                           IN :routed_qids
                                       AND rr.external_id LIKE 'article:%')))
                        AS has_article,
                    EXISTS (SELECT 1 FROM generation_model_links l
                            WHERE l.model_id = m.id
                              AND l.superseded_by IS NULL) AS has_links,
                    EXISTS (SELECT 1 FROM catalogue_periods p
                            JOIN configurations c ON c.catalogue_period_id = p.id
                            WHERE p.model_id = m.id
                              AND c.generation_id IS NOT NULL) AS has_placed
                  FROM models m
                )
                SELECT
                  count(*) FILTER (WHERE has_configs),
                  count(*) FILTER (WHERE has_configs AND has_qid),
                  count(*) FILTER (WHERE has_configs AND has_qid AND has_article),
                  count(*) FILTER (WHERE has_configs AND has_qid AND has_article
                                     AND has_links),
                  count(*) FILTER (WHERE has_configs AND has_qid AND has_article
                                     AND has_links AND has_placed)
                FROM stages
                """
            ).bindparams(
                bindparam("routed", expanding=True),
                bindparam("routed_qids", expanding=True),
            ),
            {"routed": routed, "routed_qids": routed_qids},
        ).one()
        print("coverage funnel (models, cumulative):")
        for label, n in zip(
            (
                "with configurations",
                "+ QID attached/routed",
                "+ nameplate article landed",
                "+ linked generations",
                "+ placed configurations",
            ),
            funnel,
            strict=True,
        ):
            print(f"  {label:<28}: {n}")

        placed, total, timed, gens, links = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM configurations WHERE generation_id IS NOT NULL),
                  (SELECT count(*) FROM configurations),
                  (SELECT count(*) FROM generations WHERE start_year IS NOT NULL),
                  (SELECT count(*) FROM generations),
                  (SELECT count(*) FROM generation_model_links WHERE superseded_by IS NULL)
                """
            )
        ).one()
        print(f"configurations placed         : {placed}/{total}")
        print(f"generations with spans        : {timed}/{gens} ({links} model links)")

        print("match decisions by outcome:")
        for pass_name, outcome, n in s.execute(
            text(
                """
                SELECT pass_name, outcome, count(*) FROM match_decisions
                GROUP BY 1, 2 ORDER BY 1, 3 DESC
                """
            )
        ):
            print(f"  {pass_name:<16} {outcome:<28}: {n}")

        # Registry + address health (ADR 0019 §3): the always-on loud check,
        # run where the data exists - CI's registries are monkeypatched.
        problems: list[str] = []
        live_pairs = {
            f"{co}/{m}"
            for co, m in s.execute(
                text(
                    "SELECT co.slug, m.slug FROM models m JOIN companies co ON co.id = m.company_id"
                )
            )
        }
        for name, entries in (
            ("WIKIDATA_MODEL_MATCHES", policy.WIKIDATA_MODEL_MATCHES.items()),
            ("WIKIDATA_MODEL_NEGATIVES", policy.WIKIDATA_MODEL_NEGATIVES),
            ("SECTION_ARTICLE_MODELS", policy.SECTION_ARTICLE_MODELS.items()),
        ):
            for key, pair in entries:
                if pair not in live_pairs:
                    problems.append(f"{name} ({key}, {pair!r}): pair not live")
        known_qids = {
            qid
            for (qid,) in s.execute(
                text("SELECT external_id FROM external_ids WHERE external_id LIKE 'Q%'")
            )
        }
        for name, qids in (
            ("NOT_A_GENERATION", policy.NOT_A_GENERATION),
            ("IDENTITY_MERGES canonical", set(policy.IDENTITY_MERGES.values())),
        ):
            for qid in qids:
                if qid not in known_qids:
                    problems.append(f"{name} {qid}: no external_ids row")
        shadowed = s.execute(
            text(
                """
                SELECT a.entity_kind, a.slug FROM slug_aliases a
                WHERE EXISTS (
                  SELECT 1 FROM companies c
                  WHERE a.entity_kind = 'company' AND c.slug = a.slug
                ) OR EXISTS (
                  SELECT 1 FROM models m
                  WHERE a.entity_kind = 'model' AND m.slug = a.slug
                    AND m.company_id = a.scope_company_id
                ) OR EXISTS (
                  SELECT 1 FROM model_lines l
                  WHERE a.entity_kind = 'model_line' AND l.slug = a.slug
                    AND l.company_id = a.scope_company_id
                ) OR EXISTS (
                  SELECT 1 FROM generations g
                  WHERE a.entity_kind = 'generation' AND g.slug = a.slug
                    AND g.company_id = a.scope_company_id
                ) OR EXISTS (
                  SELECT 1 FROM configurations cf
                  WHERE a.entity_kind = 'configuration' AND cf.slug = a.slug
                )
                """
            )
        ).all()
        problems.extend(f"alias shadowed by a live {k} slug: {slug}" for k, slug in shadowed)
        aliases = s.execute(text("SELECT count(*) FROM slug_aliases")).scalar()
        print(f"slug aliases                  : {aliases}")
        if problems:
            print("REGISTRY/ADDRESS HEALTH: PROBLEMS")
            for p in problems:
                print(f"  !! {p}")
        else:
            print("registry/address health       : ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
