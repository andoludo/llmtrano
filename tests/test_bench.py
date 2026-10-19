from llmtrano.bench import (
    BENCH_HEADING,
    Prompt,
    Sample,
    build_prompts,
    insert_into_measurements,
    markdown_table,
    messages_for,
    model_flags,
    summarise,
    with_nonce,
)


def _sample(mode: str, ttft: float, cached: int) -> Sample:
    return Sample("m", "p", mode, ttft, 30.0, 500, 256, cached, 400.0, 31.0)


def test_nonce_goes_first_in_system_prompt_when_present() -> None:
    prompt = with_nonce(Prompt("x", "system text", "question"), "abc")
    assert prompt.system is not None and prompt.system.startswith("[abc]")
    assert prompt.user == "question"


def test_nonce_goes_first_in_user_prompt_without_system() -> None:
    prompt = with_nonce(Prompt("x", None, "question"), "abc")
    assert prompt.user.startswith("[abc]")


def test_messages_order() -> None:
    assert [m["role"] for m in messages_for(Prompt("x", "s", "u"))] == ["system", "user"]
    assert [m["role"] for m in messages_for(Prompt("x", None, "u"))] == ["user"]


def test_prompts_grow_in_length() -> None:
    short, medium, prefix = build_prompts()
    assert len(short.user) < len(medium.user)
    assert prefix.system is not None and len(prefix.system) > len(medium.user)


def test_summarise_takes_median_per_group() -> None:
    rows = summarise([_sample("cold", 1.0, 0), _sample("cold", 3.0, 0), _sample("cold", 2.0, 0)])
    assert len(rows) == 1
    assert rows[0]["TTFT s"] == "2.00"
    assert rows[0]["TTFT range s"] == "1.00-3.00"


def test_markdown_table_shape() -> None:
    table = markdown_table([{"A": "1", "B": "2"}])
    assert table.splitlines() == ["| A | B |", "| --- | --- |", "| 1 | 2 |"]


def test_insert_before_next_section() -> None:
    text = f"# M\n\n{BENCH_HEADING}\n\nintro\n\n## Next\n"
    result = insert_into_measurements(text, "### Run X\n")
    assert result.index("### Run X") < result.index("## Next")
    assert result.index("intro") < result.index("### Run X")


def test_model_flags_read_from_config() -> None:
    flags = model_flags("qwen3.6-35b-a3b")
    assert "--n-cpu-moe" in flags
    assert "--model" not in flags
    assert "#" not in flags
