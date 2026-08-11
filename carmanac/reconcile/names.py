"""Name primitives, below every pass.

Matching needs these to decide identity; addressing needs them to compose a
page's name. Neither should import the other, so the shared piece lives here
and depends on nothing.
"""

from __future__ import annotations


def normalize_name(name: str) -> str:
    """Casefold and strip everything but letters and digits: 'ASTON MARTIN'
    == 'Aston Martin' == 'aston-martin'. Mechanical on purpose - anything
    smarter is rung 3's job (candidates), never auto-accepted."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())
