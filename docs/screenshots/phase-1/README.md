# Phase 1 ui screenshots

Taken on 2026-10-20 with Playwright in the cloud sandbox: the real api, Postgres checkpointer, Valkey and
worker (OpenModelica 1.26.9, Buildings 13.0.0), and a **scripted stub in place of the LLM** (no GPU there):
the extraction answers, replies and the explanation are canned texts. They show the ui and the workflow,
not the quality of a model; the live run is the eval of step 1.14 on the GPU machine.

1. `1-first-turn.png`: the tutorial house described in one message; the card fills with "you said" and
   "assumed" values, the planner asks for the construction period and the airtightness with answer buttons.
2. `2-ready-to-confirm.png`: after the "2006 to 2020" button, the Brief is ready and Confirm is active.
3. `3-simulated-and-explained.png`: check, one-year simulation and KPIs (3,417 kWh, the same as simulating the
   compiled Brief directly), the KPI table drawn by code and the explanation.
