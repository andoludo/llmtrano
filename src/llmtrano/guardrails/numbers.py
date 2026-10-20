"""Number guardrail (#49 output guardrail, #46 grounding): every number in an LLM answer must be a known
value.

Known values are the numbers of the KPI JSON and of the Brief. A number in the text matches when it is within
1% of a known value (relative), or is the same integer after rounding. Numbers are read in English, Dutch and
French notation: "6,085", "6.085" and "6 085" are thousands, "60,9" and "60.9" decimals. A number written in
kW or MWh is also compared at 1000 times its value (W and kWh are the units of the KPI JSON).
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

RELATIVE_TOLERANCE = 0.01
# A number with optional thousands groups ("6,085", "6.085", "6 085") or decimals ("60.9", "60,9").
NUMBER = re.compile(
    r"(?<![\w.,:])"
    r"(?P<number>\d{1,3}(?:(?P<sep>[,.\u00a0\u202f ])\d{3})(?:(?P=sep)\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![\w])"
)
# Not claims about the house: list markers ("1."), hours ("7h", "23:00") and the period ("one year").
IGNORED_CONTEXT = re.compile(r"^\s*(?:h\b|u\b|:\d{2}|am\b|pm\b)", re.IGNORECASE)


THOUSAND_UNITS = re.compile(r"^\s*(?:kw\b|mwh\b)", re.IGNORECASE)  # kW for W, MWh for kWh


@dataclass(frozen=True, slots=True)
class FoundNumber:
    text: str
    value: float
    scaled: bool = False  # written in kW or MWh: 1000 times the value is compared too


def parse_number(token: str) -> float:
    """``token`` as a float. A single separator followed by exactly three digits is a thousands separator."""
    groups = re.split(r"[,.\u00a0\u202f ]", token)
    separators = re.findall(r"[,.\u00a0\u202f ]", token)
    if not separators:
        return float(token)
    if len(separators) == 1:
        if separators[0] in " \u00a0\u202f" or (len(groups[1]) == 3 and token[0] != "0"):
            return float("".join(groups))  # "6,085", "6.085", "6 085"
        return float(token.replace(",", "."))  # "60,9", "60.9"
    *integer, last = groups
    if len(last) == 3 and len(set(separators)) == 1:  # "1,234,567"
        return float("".join(groups))
    return float("".join(integer) + "." + last)  # "6.085,5" or "6,085.5"


def numbers_in(text: str) -> list[FoundNumber]:
    found = []
    for match in NUMBER.finditer(text):
        token = match.group("number").rstrip(".,")
        after = text[match.end() : match.end() + 4]
        if IGNORED_CONTEXT.match(after):
            continue
        found.append(
            FoundNumber(text=token, value=parse_number(token), scaled=bool(THOUSAND_UNITS.match(after)))
        )
    return found


def known_values(*sources: Any) -> set[float]:
    """Every number in the given JSON-like structures (dicts, lists, numbers; strings are scanned too)."""
    values: set[float] = set()

    def walk(item: Any) -> None:
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, int | float):
            if math.isfinite(item):
                values.add(float(item))
        elif isinstance(item, Mapping):
            for value in item.values():
                walk(value)
        elif isinstance(item, Iterable) and not isinstance(item, str | bytes):
            for value in item:
                walk(value)
        elif isinstance(item, str):
            values.update(number.value for number in numbers_in(item))

    for source in sources:
        walk(source)
    return values


def matches(value: float, known: Iterable[float]) -> bool:
    for reference in known:
        if math.isclose(value, reference, rel_tol=RELATIVE_TOLERANCE):
            return True
        if value == int(value) and round(reference) == value:  # "6085 kWh" for 6085.48
            return True
    return False


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    passed: bool
    wrong: list[str]  # the numbers, as written, that match no known value


def check_numbers(text: str, kpis: Mapping[str, Any], brief: Mapping[str, Any]) -> GuardrailResult:
    known = known_values(kpis, brief)
    wrong = [
        number.text
        for number in numbers_in(text)
        if not (matches(number.value, known) or (number.scaled and matches(number.value * 1000, known)))
    ]
    return GuardrailResult(passed=not wrong, wrong=list(dict.fromkeys(wrong)))


def retry_note(wrong: Iterable[str]) -> str:
    listed = ", ".join(wrong)
    return (
        f"Your previous answer contained numbers that are not in the Results or the House: {listed}. "
        "Write it again using only numbers copied from the Results and the House, rounded at most."
    )


def fallback_text(kpis: Mapping[str, Any]) -> str:
    """The fixed sentence shown instead of an LLM text that failed the guardrail twice."""
    return (
        f"The simulated house needs {kpis['heating_energy_kwh']:.0f} kWh of heat per year, "
        f"{kpis['heating_energy_kwh_per_m2']:.1f} kWh per m2 of heated floor area, with a peak heating "
        f"power of {kpis['peak_heating_w']:.0f} W. The table shows the results; the explanation could not be "
        "checked and is not shown."
    )
