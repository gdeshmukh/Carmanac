"""The coverage fixture's own hygiene.

The fixture guards the fetch; these tests guard the fixture - a malformed or
shrunken KNOWN_MARQUES would quietly weaken the coverage check it powers.
The actual coverage enforcement runs after every landing
(`python -m carmanac.ingest.wikidata.land` exits nonzero on a miss); it
cannot run in CI, where no landed data exists.
"""

from __future__ import annotations

import re

from carmanac.ingest.wikidata.coverage import KNOWN_MARQUES, NOT_IN_WIKIDATA


def test_fixture_is_well_formed():
    assert all(re.fullmatch(r"Q\d+", qid) for qid, _ in KNOWN_MARQUES)
    assert all(name.strip() == name and name for _, name in KNOWN_MARQUES)


def test_fixture_has_no_duplicate_qids():
    qids = [q for q, _ in KNOWN_MARQUES]
    assert len(qids) == len(set(qids))


def test_fixture_spans_the_risk_register_axes():
    """The risk register names the coverage weak spots: Soviet-era, Chinese,
    Indian, Brazilian, JDM. A fixture that shrinks below these spot checks
    has lost the axes it exists to defend."""
    names = {name for _, name in KNOWN_MARQUES}
    for probe in (
        "Pontiac",
        "TVR",
        "Datsun",
        "Lada",
        "Chery",
        "Tata Motors",
        "Gurgel",
        "Peugeot",
        "Tesla",
        "Alpina",
    ):
        assert probe in names, f"fixture lost its {probe} spot-check"
    assert len(KNOWN_MARQUES) >= 200


def test_documented_absences_carry_notes():
    assert all(note for _, note in NOT_IN_WIKIDATA)
