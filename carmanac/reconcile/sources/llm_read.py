"""The LLM read as a source (ADR 0017, amended 2026-09-17): the text a
model is shown, the question it is asked, and the gate its answer passes.

The model classifies; it never authors. It sees one page's text, the
generations already held for the nameplate, and the candidate
configurations with their ids, and answers with the generations on the
page, the cars in each, and the leaves per car, every item carrying a
quote. `verify` keeps an item only when its quote is a verbatim substring
of that text and itself states the item's codes, years and name, and keeps
a leaf only when it was offered and its model year sits inside the years
its car states. The script that asks and the pass that lands run this same
gate over the same text, so nothing the model made up can reach a row.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Bumped when the normalisation or the prompt changes: a read at an older
# version is a different question, and the script asks again.
PROMPT_VERSION = "1"

_STRIP = (
    re.compile(r"<!--.*?-->", re.S),
    re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S),
    re.compile(r"\[\[(?:File|Image):[^\[\]]*(?:\[\[[^\[\]]*\]\][^\[\]]*)*\]\]"),
)
_LINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_BOLD = re.compile(r"'{2,}")
_BLANK = re.compile(r"\n{3,}")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")

SYSTEM = (
    "You are reading one Wikipedia article about a car nameplate. Reply with JSON only, in "
    'this shape:\n{"generations": [{"name": "...", "codes": ["..."], "start_year": 1989, '
    '"end_year": 1994, "quote": "...", "cars": [{"name": "...", "start_year": 1989, '
    '"end_year": 1994, "quote": "...", "leaves": [{"id": 123, "match": "exact"}]}]}]}\n\n'
    "Rules:\n"
    "- You classify what the article states. Never add, infer or complete anything the "
    "article does not say.\n"
    "- Every quote is copied verbatim from the article text, unchanged, at most 300 "
    "characters, and must itself contain the codes, the years and the name it supports.\n"
    "- List a generation only where the article presents a generation, series or era of "
    "this nameplate. Trims, engines, body styles and special editions are cars within a "
    "generation, never generations.\n"
    "- Name a generation by its chassis or platform code when the article gives one. When "
    "it is one of the known generations, use that generation's name.\n"
    "- end_year is null only where the quote says the generation is still in production. "
    "Leave a generation out if the article gives no end and does not say it continues.\n"
    "- For each generation, list the cars (models, badges, trims, body styles) the article "
    "says belong to it, each with its own quote and with years where the article gives "
    "them.\n"
    "- For each car, choose from the candidate configurations only: the ids whose line is "
    'that car ("exact"), or the nearest lines when none is exact ("closest"). Never choose '
    "a configuration whose model year falls outside the car's years. Omit a car with no "
    "candidate.\n"
)


@dataclass(frozen=True)
class Leaf:
    """One candidate configuration as the model sees it."""

    id: int
    year: int
    trim: str | None
    body: str | None
    drivetrain: str | None
    displacement_cc: int | None
    cylinders: int | None
    power_hp: int | None

    def line(self) -> str:
        engine = " ".join(
            part
            for part in (
                f"{self.displacement_cc} cc" if self.displacement_cc else "",
                f"{self.cylinders} cyl" if self.cylinders else "",
                f"{self.power_hp} hp" if self.power_hp else "",
            )
            if part
        )
        return (
            f"{self.id} | {self.year} | {self.trim or '-'} | {self.body or '-'} | "
            f"{self.drivetrain or '-'} | {engine or '-'}"
        )


@dataclass(frozen=True)
class Car:
    name: str
    start_year: int | None
    end_year: int | None
    quote: str
    leaves: tuple[tuple[int, str], ...]  # (configuration id, "exact" | "closest")


@dataclass(frozen=True)
class ReadGeneration:
    name: str
    codes: tuple[str, ...]
    start_year: int
    end_year: int | None
    quote: str
    cars: tuple[Car, ...]


@dataclass
class Verified:
    generations: list[ReadGeneration] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)

    @property
    def leaves(self) -> int:
        return sum(len(car.leaves) for g in self.generations for car in g.cars)


def page_text(wikitext: str) -> str:
    """The article as the model reads it: markup that carries no words
    removed, links reduced to their display text, whitespace settled."""
    text = wikitext
    for pattern in _STRIP:
        text = pattern.sub("", text)
    text = _BOLD.sub("", _LINK.sub(r"\1", text))
    text = "\n".join(" ".join(line.split()) for line in text.split("\n"))
    return _BLANK.sub("\n\n", text).strip()


def build_messages(
    title: str,
    text: str,
    held: list[tuple[str, list[str], int | None, int | None]],
    leaves: list[Leaf],
) -> list[dict]:
    known = "\n".join(
        f"{name} | {' '.join(codes) or '-'} | {start or '?'}–{end or ('present' if start else '?')}"
        for name, codes, start, end in held
    )
    user = (
        f"Article: {title}\n\n"
        f"Known generations of this nameplate (name | codes | years):\n{known or 'none'}\n\n"
        "Candidate configurations (id | model year | trim | body | drivetrain | engine):\n"
        + "\n".join(leaf.line() for leaf in leaves)
        + f"\n\nArticle text:\n{text}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse_answer(answer: str) -> object:
    try:
        return json.loads(_FENCE.sub("", answer))
    except ValueError:
        return None


def _squash(text: str) -> str:
    return " ".join(text.split())


def _year(value: object) -> int | None:
    return value if isinstance(value, int) and 1885 <= value <= 2100 else None


def verify(answer: object, text: str, offered: dict[int, int]) -> Verified:
    """Keep what the page supports. `offered` maps each candidate
    configuration id to its model year; nothing else can be chosen."""
    out = Verified()
    if not isinstance(answer, dict) or not isinstance(answer.get("generations"), list):
        out.dropped.append({"reason": "malformed answer"})
        return out
    page = _squash(text)

    def quoted(quote: object) -> bool:
        return isinstance(quote, str) and 0 < len(quote) <= 400 and _squash(quote) in page

    def states(quote: str, *needles: object) -> bool:
        return all(str(n).casefold() in quote.casefold() for n in needles)

    claims: dict[int, int] = {}
    for g in answer["generations"]:
        if not isinstance(g, dict):
            continue
        name, quote = g.get("name"), g.get("quote")
        codes = tuple(c.strip() for c in g.get("codes") or [] if isinstance(c, str) and c.strip())
        start, end = _year(g.get("start_year")), _year(g.get("end_year"))
        if not isinstance(name, str) or not name.strip():
            out.dropped.append({"generation": name, "reason": "no name"})
            continue
        if not quoted(quote):
            out.dropped.append({"generation": name, "reason": "quote not on the page"})
            continue
        if start is None or not states(quote, start, *codes):
            out.dropped.append(
                {"generation": name, "reason": "quote does not state the codes and start"}
            )
            continue
        if end is None and g.get("end_year") is not None:
            out.dropped.append({"generation": name, "reason": "end year out of range"})
            continue
        if end is None and "present" not in quote.casefold():
            out.dropped.append({"generation": name, "reason": "open end without 'present'"})
            continue
        if end is not None and (end < start or not states(quote, end)):
            out.dropped.append({"generation": name, "reason": "quote does not state the end"})
            continue
        cars: list[Car] = []
        for car in g.get("cars") or []:
            if not isinstance(car, dict):
                continue
            car_name, car_quote = car.get("name"), car.get("quote")
            c_start, c_end = _year(car.get("start_year")), _year(car.get("end_year"))
            if (
                not isinstance(car_name, str)
                or not quoted(car_quote)
                or not states(car_quote, car_name)
            ):
                out.dropped.append(
                    {"generation": name, "car": car_name, "reason": "car not quoted"}
                )
                continue
            if not states(car_quote, *(y for y in (c_start, c_end) if y is not None)):
                out.dropped.append(
                    {"generation": name, "car": car_name, "reason": "years not in quote"}
                )
                continue
            low, high = c_start or start, c_end or end
            leaves: list[tuple[int, str]] = []
            for leaf in car.get("leaves") or []:
                if not isinstance(leaf, dict):
                    continue
                leaf_id, match = leaf.get("id"), leaf.get("match")
                if leaf_id not in offered or match not in ("exact", "closest"):
                    out.dropped.append({"car": car_name, "leaf": leaf_id, "reason": "not offered"})
                    continue
                if not (low <= offered[leaf_id] <= (high or 2100)):
                    out.dropped.append(
                        {"car": car_name, "leaf": leaf_id, "reason": "outside the car's years"}
                    )
                    continue
                claims[leaf_id] = claims.get(leaf_id, 0) + 1
                leaves.append((leaf_id, match))
            cars.append(Car(car_name.strip(), c_start, c_end, car_quote, tuple(leaves)))
        out.generations.append(ReadGeneration(name.strip(), codes, start, end, quote, tuple(cars)))

    # A leaf two cars claim is a guess either way; neither keeps it.
    twice = {leaf_id for leaf_id, n in claims.items() if n > 1}
    if twice:
        out.dropped.extend(
            {"leaf": leaf_id, "reason": "claimed twice"} for leaf_id in sorted(twice)
        )
        out.generations = [
            ReadGeneration(
                g.name,
                g.codes,
                g.start_year,
                g.end_year,
                g.quote,
                tuple(
                    Car(
                        c.name,
                        c.start_year,
                        c.end_year,
                        c.quote,
                        tuple(leaf for leaf in c.leaves if leaf[0] not in twice),
                    )
                    for c in g.cars
                ),
            )
            for g in out.generations
        ]
    return out
