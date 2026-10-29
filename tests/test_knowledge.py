"""Grounded references: the table loads with sources, retrieval matches the house, the prompt gets them."""

from llmtrano.brief.model import BuildingType, HouseBrief, Period
from llmtrano.knowledge import Reference, load_references, retrieve
from llmtrano.llm.reply import interpret_input

REFERENCE_HOUSE = HouseBrief(building_type=BuildingType.semi_detached, construction_period=Period.y1946_1970)


def test_every_reference_has_a_source_and_a_unit() -> None:
    references = load_references()
    assert len(references) >= 7
    assert all("docs/" in r.source for r in references)
    assert all(r.unit and r.conditions for r in references)


def test_retrieval_matches_the_reference_house_before_and_after() -> None:
    before = retrieve(REFERENCE_HOUSE)
    assert [r.id for r in before] == [
        "trano_reference_before_heating",
        "trano_reference_before_design_loss",
        "trano_reference_before_gas",
    ]
    after = retrieve(REFERENCE_HOUSE, renovated=True)
    assert [r.value for r in after] == [17.6, 21, 4.8]
    recent = HouseBrief(building_type=BuildingType.detached, construction_period=Period.y2006_2020)
    assert retrieve(recent) == [load_references()[-1]]  # the tutorial house
    old = HouseBrief(building_type=BuildingType.detached, construction_period=Period.before_1946)
    assert retrieve(old) == []
    assert retrieve(HouseBrief()) == []  # nothing known: nothing to compare with


def test_reference_line_and_prompt_section() -> None:
    reference: Reference = retrieve(REFERENCE_HOUSE)[0]
    line = reference.line()
    assert line.startswith("trano's reference semi-detached house") and "130 kWh/m2" in line
    assert "(source:" in line
    text = interpret_input(
        kpis={"heating_energy_kwh": 1.0, "heating_energy_kwh_per_m2": 1.0, "peak_heating_w": 1.0},
        brief=REFERENCE_HOUSE,
        provenance={},
        assumptions=["a"],
        language="English",
        references=[line],
    )
    assert "References (published results for houses like this one, with their source):\n- trano" in text
    assert text.index("References") < text.index("Assumptions:")
