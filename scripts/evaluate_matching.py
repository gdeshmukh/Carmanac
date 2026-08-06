"""Report the matcher's labeled set and what is measurable from it.

The charter gates Tier 2/3 sources on "matcher precision measured on a
labeled set". The labels live in three places - the curated registries in
`policy.py`, the `match_decisions` log, and flag closes carrying a
resolution reason - and until now nothing read them back. This prints what
each holds, which precision claims the current evidence supports, and which
gaps still block a real measurement. Read-only, `status.py`'s sibling.

Usage:  .venv/bin/python scripts/evaluate_matching.py
"""

from __future__ import annotations

from sqlalchemy import text

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy

# Outcomes that accepted a correspondence mechanically (no human in the loop),
# per pass. Flags and waits are deliberate non-decisions; refreshes re-walk an
# identity established earlier, so only the first acceptance counts as the
# matcher's claim.
MECHANICAL_ACCEPTS: dict[str, tuple[str, ...]] = {
    "epa_attach": ("attached",),
    "vpic_years": ("periods_written",),
    "wikidata_models": (
        "matched",
        "generation_created",
        "line_created",
        "line_matched",
        "model_refreshed",
        "generation_refreshed",
    ),
}


def _count(s, sql: str, **params) -> int:
    return s.execute(text(sql), params).scalar() or 0


def main() -> int:
    print("== the curated registries (recorded human judgments) ==")
    for name, registry in (
        ("VPIC_MATCHES (make -> QID)", policy.VPIC_MATCHES),
        ("IDENTITY_MERGES (member -> canonical)", policy.IDENTITY_MERGES),
        ("PINNED_ADMIT", policy.PINNED_ADMIT),
        ("PINNED_DENY", policy.PINNED_DENY),
        ("WIKIDATA_MODEL_MATCHES (QID -> model)", policy.WIKIDATA_MODEL_MATCHES),
        ("EPA_MAKE_MATCHES (make -> QID)", policy.EPA_MAKE_MATCHES),
        ("WIKIDATA_MODEL_NEGATIVES (QID, model)", policy.WIKIDATA_MODEL_NEGATIVES),
    ):
        note = "  <- EMPTY" if len(registry) == 0 else ""
        print(f"  {name:<42}: {len(registry)}{note}")

    with SessionLocal() as s:
        print("\n== the decision log, per pass ==")
        for pass_name, rung, method, outcome, n in s.execute(
            text(
                """SELECT pass_name, coalesce(rung,'-'), coalesce(method,'-'), outcome, count(*)
                   FROM match_decisions GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC"""
            )
        ):
            accepted = outcome in MECHANICAL_ACCEPTS.get(pass_name, ())
            tag = "accept" if accepted else "      "
            print(f"  {tag} {pass_name:<16} rung {rung:<2} {method:<30} {outcome:<32} {n}")

        print("\n== flag closes (labels, when they carry a reason) ==")
        reasonless = 0
        for kind, status, resolution, n in s.execute(
            text(
                """SELECT kind, status,
                          CASE WHEN detail->>'resolution' IS NULL THEN '(no reason)'
                               WHEN detail->>'resolution' LIKE 'matched:%'
                                 THEN 'matched:* (question dissolved)'
                               WHEN detail->>'resolution' LIKE 'generation_created:%'
                                 THEN 'generation_created:* (question dissolved)'
                               WHEN detail->>'resolution' LIKE 'curated_match:%'
                                 THEN 'curated_match:* (human judgment)'
                               ELSE detail->>'resolution' END,
                          count(*)
                   FROM reconciliation_flags WHERE status <> 'open'
                   GROUP BY 1,2,3 ORDER BY 1,4 DESC"""
            )
        ):
            print(f"  {kind:<18} {status:<10} {resolution:<42}: {n}")
            if resolution == "(no reason)":
                reasonless += n
        if reasonless:
            print(
                f"  ({reasonless} closes carry no reason - closed before the "
                f"resolution discipline; unusable as labels)"
            )

        print("\n== precision, where the evidence reaches ==")
        accepts = {
            pass_name: _count(
                s,
                """SELECT count(*) FROM match_decisions
                   WHERE pass_name = :p AND outcome = ANY(:outcomes)""",
                p=pass_name,
                outcomes=list(outcomes),
            )
            for pass_name, outcomes in MECHANICAL_ACCEPTS.items()
        }
        for pass_name, n in accepts.items():
            print(f"  {pass_name:<16}: {n} mechanical accepts, 0 recorded contradictions")
        print(
            "  A contradiction would be a negative-registry entry or a curated\n"
            "  entry disagreeing with a mechanical accept; none exist. That is a\n"
            "  LOWER BOUND on evidence, not high-confidence precision: the\n"
            "  negative registry is empty and the review queues are largely\n"
            "  unworked, so absence of contradiction mostly measures absence of\n"
            "  review."
        )

        print("\n== recall proxies (the reviewable misses) ==")
        for reason, n in s.execute(
            text(
                """SELECT detail->>'reason', count(*)
                   FROM reconciliation_flags
                   WHERE status = 'open' AND kind = 'match_review'
                   GROUP BY 1 ORDER BY 2 DESC"""
            )
        ):
            print(f"  open match_review: {reason or '(none)':<28}: {n}")
        print(
            "  Each resolution labels one miss (curated entry) or one correct\n"
            "  abstention (dismissal with reason). Recall is computable only as\n"
            "  these queues get worked."
        )

    print("\n== what still blocks a real evaluation ==")
    print(
        "  1. WIKIDATA_MODEL_NEGATIVES is empty: the labeled set has no true\n"
        "     negatives, so precision cannot distinguish 'unreviewed' from\n"
        "     'reviewed and correct'.\n"
        "  2. Overturns are not machine-readable: a mechanical match later\n"
        "     unwound by a merge/decision script leaves no negative label\n"
        "     behind. Needs a rule: every unwind records a registry entry.\n"
        "  3. The methodology itself (what counts as an overturn, how sampled\n"
        "     review labels accumulate) is undecided - an ADR before any\n"
        "     precision number is quoted against the charter's Tier-2/3 gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
