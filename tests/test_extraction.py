"""Extraction pipeline: schema, validation, retry and give-up on fixture outputs; 5 live sentences."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from fakes import FakeLLM
from openai import AsyncOpenAI

from llmtrano.brief.model import BRIEF_FIELDS, FieldProvenance, HouseBrief, Source
from llmtrano.llm.extraction import (
    extraction_input,
    extraction_schema,
    run_extraction,
    validate_extraction,
)

FIXTURES: list[dict[str, Any]] = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "extraction.yaml").read_text()
)


def test_schema_is_flat() -> None:
    schema = extraction_schema()
    text = json.dumps(schema)
    assert "$ref" not in text and "anyOf" not in text
    fact = schema["properties"]["facts"]["items"]
    assert fact["properties"]["field"]["enum"] == list(BRIEF_FIELDS)
    assert fact["properties"]["value"] == {"type": "string"}
    assert set(fact["required"]) == {"field", "value", "scenario", "evidence", "confidence"}
    assert schema["required"] == ["facts", "user_says_complete"]


@pytest.mark.parametrize("case", FIXTURES, ids=[case["name"] for case in FIXTURES])
async def test_pipeline_on_fixture_outputs(case: dict[str, Any]) -> None:
    llm = FakeLLM().queue("extract", *case["outputs"])
    dynamic = extraction_input(
        brief=HouseBrief(), provenance={}, last_question=case.get("last_question"), message=case["message"]
    )
    result = await run_extraction(cast(AsyncOpenAI, llm), dynamic, model="m", turn=3)
    baseline = {f.field: f.value for f in result.facts if f.scenario == "baseline"}
    renovation = {f.field: f.value for f in result.facts if f.scenario == "renovation"}
    assert baseline == case["expected"]
    assert renovation == case.get("renovation", {})
    assert result.user_says_complete is case.get("complete", False)
    assert result.gave_up is case.get("gave_up", False)
    assert len(result.calls) == len(llm.calls("extract")) == case["calls"]
    assert all(f.turn == 3 for f in result.facts)


async def test_retry_shows_the_problems() -> None:
    case = next(c for c in FIXTURES if c["name"] == "bad_value_fixed_on_retry")
    llm = FakeLLM().queue("extract", *case["outputs"])
    await run_extraction(cast(AsyncOpenAI, llm), "dynamic", model="m", turn=1)
    retry = llm.calls("extract")[1]["messages"][-1]["content"]
    assert retry.startswith("dynamic\n\nYour previous answer had these problems:")
    assert "north-east" in retry


def test_validation_messages() -> None:
    raw, facts, problems = validate_extraction('{"facts": [], "user_says_complete": "maybe"}', 1)
    assert raw is None and facts == []
    assert problems == ["user_says_complete: Input should be a valid boolean, unable to interpret input"]


def test_extraction_input_shows_only_user_values() -> None:
    brief = HouseBrief(heated_floor_area_m2=120, room_height_m=2.6)
    provenance = {
        "heated_floor_area_m2": FieldProvenance(source=Source.user, evidence="120 m2"),
        "room_height_m": FieldProvenance(source=Source.default, basis="trano"),
    }
    text = extraction_input(
        brief=brief, provenance=provenance, last_question="How many storeys?", message="two"
    )
    assert text == (
        "Known so far from the person: heated_floor_area_m2=120.0\n"
        "Last question asked: How many storeys?\n"
        "Message: 'two'"
    )


LIVE = [
    ("We live in a detached house built in 1985, about 150 m2.", {"building_type", "construction_period"}),
    (
        "Rijwoning uit de jaren 60 met dubbel glas en twee verdiepingen.",
        {"building_type", "glazing", "storeys"},
    ),
    ("The roof is insulated but the walls are bare.", {"roof_insulation", "wall_insulation"}),
    ("Next year we want triple glazing; now it is single glazing.", {"glazing"}),
    ("We keep it at 20 degrees from 7 in the morning to 11 at night.", {"heating_setpoint_day_c"}),
]


@pytest.mark.llm
@pytest.mark.parametrize(("message", "fields"), LIVE)
async def test_live_extraction(message: str, fields: set[str]) -> None:
    """Needs the llm service (`docker compose --profile gpu up -d`)."""
    from llmtrano.llm.client import make_llm_client
    from llmtrano.settings import Settings

    settings = Settings()
    llm = make_llm_client(settings)
    dynamic = extraction_input(brief=HouseBrief(), provenance={}, last_question=None, message=message)
    result = await run_extraction(llm, dynamic, model=settings.llm_default_model, turn=1)
    assert not result.gave_up
    assert fields <= {fact.field for fact in result.facts}
