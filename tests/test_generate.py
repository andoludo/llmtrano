"""The eval case generator: deterministic, parser-consistent phrases, thirty cases in three languages."""

from pathlib import Path

import pytest

from llmtrano.brief.parse import parse_value, period_of_year
from llmtrano.evals.cases import load_cases
from llmtrano.evals.generate import ARCHETYPES, LANGUAGES, generate_all, opening_text, phrase, write_cases
from llmtrano.llm.reply import detect_language

CASES = Path(__file__).parents[1] / "evals" / "cases"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("archetype", ARCHETYPES, ids=[a.key for a in ARCHETYPES])
def test_every_phrase_parses_to_its_value(archetype: object, language: str) -> None:
    """A perfect extraction of the generated text gives exactly the expected Brief values."""
    assert hasattr(archetype, "values") and hasattr(archetype, "renovation")
    for name, value in {**archetype.values, **archetype.renovation}.items():  # type: ignore[attr-defined]
        got = parse_value(name, phrase(language, name, value))  # type: ignore[arg-type]
        got = getattr(got, "value", got)
        expected = period_of_year(value).value if name == "construction_period" else value
        assert str(got) == str(expected) or float(got) == float(expected), (name, language)


@pytest.mark.parametrize("language", LANGUAGES)
def test_openings_are_detected_in_their_language(language: str) -> None:
    other = "Dutch" if language == "English" else "English"
    for archetype in ARCHETYPES:
        assert detect_language(opening_text(archetype, language), other) == language, archetype.key  # type: ignore[arg-type]


def test_generation_is_deterministic_and_committed(tmp_path: Path) -> None:
    written = write_cases(tmp_path)
    assert len(written) == 30
    for path in written:
        committed = CASES / path.name
        assert committed.exists(), f"run `uv run python -m llmtrano.evals.generate` ({path.name} missing)"
        assert committed.read_text() == path.read_text(), f"{path.name} is stale: regenerate the cases"


def test_generated_cases_load_with_the_hand_written_ones() -> None:
    cases = load_cases(CASES)
    generated = [case for case in cases if case.generated]
    assert len(generated) == 30 and len(cases) >= 45
    assert {case.language for case in generated} == {"English", "Dutch", "French"}
    assert sum(case.outcome == "compare" for case in generated) == 6
    assert sum(case.outcome == "unsupported" for case in generated) == 3
    ids = [case["id"] for case in generate_all()]
    assert len(set(ids)) == 30 and all(i.startswith("gen_") for i in ids)
