# Phase 7A — Real Controlled Five-Gate Canary Design

**Started**: 2026-07-18
**Starting HEAD**: `5c44da7` (Add VERA4 fragmented universes offline qualification)
**Baseline**: 2494 passed, 0 failed, fake benchmark 21/21
**Plan Closed Loop contract version**: stays at `0.8` (Phase 7A does NOT bump the contract).

## Goal

Wire the existing production planning stack — five-gate controlled Plan Closed
Loop, fragmented universes, strict structured patch output, real LLM clients,
and (optionally) real OpenMC export/geometry-debug/smoke — into a single
reactor-neutral campaign harness that can produce truthful evidence for
VERA3 (3A/3B) and VERA4 cases.

Phase 7A only **proves** the wiring end-to-end and produces real evidence.
It deliberately does **not**:
- bump the plan-closed-loop contract version,
- flip `_DEFAULT_EXECUTABLE_PLAN_GATES` to the five-gate default,
- rewrite the runtime repair architecture,
- hardcode any benchmark-specific physics values,
- add reference / gold / monolithic fallback,
- declare production-ready stability.

## Scope

### What changes

1. **Reactor-neutral `RealCampaignCaseSpec`** — replaces the implicit
   VERA3B-only config in `real_campaign.py`. Carries `case_id`, `input_path`,
   `operating_state`, `benchmark_label`, `model`, `output_dir`,
   `planning_stage`, optional `human_answer_file`, `acceptance_profile`,
   `metadata`. VERA3/VERA4 names live only in case registries, fixtures,
   acceptance callbacks, and reports — never inside the production code path.

2. **`RealCampaignClientBundle`** gains two real clients:
   - `plan_reviewer_client` — used by every gate reviewer (Facts,
     Material–Universe, Placement, Axial Geometry, Assembled Plan).
   - `plan_repair_client` — Phase-3B typed retry producer.
   Both created fresh per run and routed through `LLMCallRecorder`.

3. **Explicit five-gate `PlanClosedLoopPolicy`** is constructed by the
   campaign and passed to `build_plan_graph`. The campaign never relies on
   the CLI default gate list.

4. **Fragmented universes** flow: campaign forwards
   `universes_generation_mode`, `universe_fragment_max_tokens`,
   `large_patch_safe_output_ratio`, and `strict_structured_patch_output` to
   the graph. VERA4 canary runs in `fragmented` mode.

5. **Provider/environment detection** driven by the model prefix via the
   existing `_client_for_model` resolver and the
   `OpenAICompatibleChatClient.api_key_env` class attribute. Environment
   gaps return explicit `BLOCKED_BY_LLM_ENVIRONMENT` /
   `BLOCKED_BY_OPENMC_ENVIRONMENT` /
   `BLOCKED_BY_CROSS_SECTIONS_ENVIRONMENT` — never recorded as planning or
   smoke failures, never papered over with Fake clients.

6. **Stage modes**: `planning`, `render-compile`, `openmc-smoke`.
   - `planning` stops at Assembled Plan Gate accepted (no render).
   - `render-compile` invokes the chosen renderer, writes `model.py`, runs
     `validate_openmc_script`, compiles `model.py` — no XML, no OpenMC.
   - `openmc-smoke` performs the real XML export, real geometry debug and
     a low-cost real OpenMC run, reading actual statepoint evidence.

7. **`--human-answer-file`** — typed, hashable JSON consumed only when a
   gate requests human confirmation. Absent answers →
   `AWAITING_HUMAN` (never silently passed or auto-generated).

8. **Unified LLM budget estimator** — `estimate_real_campaign_llm_budget`
   computes reserves for patch generation, universe manifest, universe
   fragments, gate reviews, plan repair and runtime diagnosis/proposal.
   `--max-llm-calls` always wins. Resume never refreshes spent budget;
   overrun returns `SAFE_STOP_BUDGET`.

9. **Resume fingerprint** — bound to git SHA, input hash, requirement
   hash, human-answer hash, model/provider, reasoning effort, output mode,
   plan policy hash, enabled gates, review modes, universe generation
   mode, fragment token budget, material policy, runtime mode, and the
   OpenMC cross-section environment fingerprint. Mismatch →
   `CONFIG_MISMATCH` (no fragment, gate or run reuse).

10. **Truthfulness evidence** — extended `RealCampaignRunResult` carrying
    per-gate statuses/hashes/review+repair+retry counts, fragmented
    universe telemetry, reviewer/repair call counts,
    `final_gate_accepted_before_render`, stage timestamps, policy hash and
    human-answer hash. `validate_real_run_truthfulness` rejects fake
    clients, plain-text fallback, reference patches, gold/benchmark
    few-shots, monolithic fallback, partial fragment exposure, missing
    reviewer calls, auto-accepted gates, stale assembled plan execution,
    persisted reasoning content and unverifiable provider evidence.

11. **Artifacts** — every run writes
    `campaign_config.json`, `environment_evidence.json`,
    `human_answer_provenance.json`, `llm_call_manifest.json`,
    `llm_budget.json`, `truthfulness_evidence.json`,
    `workflow/incremental/plan_build_state.json`,
    `workflow/incremental/large_patch_generation/universes/summary.json`
    (when fragmented), `five_gate_status.json`, `five_gate_timeline.json`,
    `five_gate_hashes.json`, `plan_reviewer_calls.json`,
    `plan_retry_summary.json`, `planning_final_disposition.json`,
    optional `render_summary.json`, `openmc_backend_evidence.json`,
    `runtime_summary.json`, `run_result.json`. Campaign-level:
    `campaign_manifest.json`, `campaign_results.json`,
    `campaign_results.csv`, `aggregate_metrics.json`,
    `qualification_report.md`.

12. **CLI** — `scripts/evaluate_plan_closed_loop_real_canary.py`
    exposing `--case`, `--input`, `--operating-state`, `--model`,
    `--output-dir`, `--stage`, `--runs`, `--confirm-real-campaign`,
    `--resume`, `--human-answer-file`, plus all five-gate and universe
    controls. `--case` is just a labelled preset; `--input` always
    overrides.

### What does NOT change

- `PlanClosedLoopPolicy` model and contract version.
- Gate implementations (Facts / Material–Universe / Placement / Axial
  Geometry / Assembled Plan).
- Runtime repair, supervisor, diagnostician, proposer logic (reused as-is).
- `_DEFAULT_EXECUTABLE_PLAN_GATES` (left untouched; will be flipped in a
  later, independent commit only after a real canary passes).
- Renderer capability matrix / assembly3d guard / axial overlay logic.
- LLM provider clients.

### Phase 7A acceptance declarations allowed after offline harness

- `P2_REAL_CONTROLLED_FIVE_GATE_CAMPAIGN_HARNESS_READY`
- `P2_REAL_CAMPAIGN_FRAGMENTED_UNIVERSES_READY`
- `P2_REAL_CAMPAIGN_TRUTHFULNESS_EVIDENCE_READY`
- `P2_REAL_CAMPAIGN_RESUME_READY`

Only **real** successful runs may declare:
- `VERA3_REAL_CONTROLLED_PLANNING_CANARY_PASSED`
- `VERA4_REAL_CONTROLLED_PLANNING_CANARY_PASSED`
- `VERA3_REAL_RENDER_COMPILE_CANARY_PASSED`
- `VERA4_REAL_RENDER_COMPILE_CANARY_PASSED`
- `VERA3_REAL_OPENMC_SMOKE_PASSED`
- `VERA4_REAL_OPENMC_SMOKE_PASSED`

Phase 7A explicitly does **not** declare:
- `P2_PLAN_CLOSED_LOOP_PRODUCTION_READY`
- `P2_DEFAULT_FIVE_GATE_CHAIN_READY`
- `VERA4_FULL_QUALIFICATION_PASSED`
- `REAL_LLM_STABILITY_ACCEPTED`

## Commit plan

1. **Modernize real campaign for five-gate controlled planning** — reactor-
   neutral `RealCampaignCaseSpec`, provider/env detection, client bundle
   with `plan_reviewer_client` + `plan_repair_client`, explicit
   `PlanClosedLoopPolicy`, runtime reuse only.
2. **Add fragmented-universe, reviewer and budget campaign integration** —
   forwards `universes_generation_mode` and friends, budget estimator,
   truthfulness gating on partial fragments.
3. **Add staged execution, truthfulness and resume evidence** — stage
   modes, typed resume fingerprint, extended result dataclass, artifact
   writers, truthfulness validator extensions, CLI.
4. **Add VERA3/VERA4 Phase 7A offline qualification** — 12 offline test
   files covering policy/bundle/fragments/budget/provider/human-answers/
   resume/truthfulness/stage-modes plus per-case offline canaries.
