# Workflow

The LangGraph graph (`llmtrano.workflow.graph`), exported with `graph.get_graph().draw_mermaid()`.
Regenerate after every change with `uv run python -m llmtrano.workflow.graph`; `tests/test_workflow.py` fails
when this file is stale.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	ingest(ingest)
	extract(extract)
	merge(merge)
	plan(plan)
	respond(respond)
	confirm(confirm)
	compile(compile)
	check(check)
	simulate(simulate)
	kpis(kpis)
	compare(compare)
	interpret(interpret)
	explain_error(explain_error)
	__end__([<p>__end__</p>]):::last
	__start__ --> ingest;
	check -.-> explain_error;
	check -.-> simulate;
	compare --> interpret;
	compile -.-> check;
	compile -.-> compare;
	compile -.-> explain_error;
	confirm -.-> compile;
	confirm -.-> ingest;
	confirm -.-> merge;
	extract --> merge;
	ingest --> extract;
	kpis -.-> compare;
	kpis -.-> compile;
	merge --> plan;
	plan --> respond;
	respond -.-> __end__;
	respond -.-> confirm;
	simulate -.-> explain_error;
	simulate -.-> kpis;
	explain_error --> __end__;
	interpret --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
