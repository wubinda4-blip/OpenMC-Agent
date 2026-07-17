# Phase 1C failure analysis

The replayed run is `data/runs/phase1b_vera4_facts_advisory_manual`.  Its CLI
entrypoint was `python -u -m openmc_agent.inspect --plan ... --plan-loop-mode
advisory`; the trace records `planner_path=incremental`, with monolithic
fallback disabled.  This is therefore an incremental failure, not evidence
about the monolithic planner.

The initial order was feature-detector-derived: `facts, materials, universes,
assembly_catalog, axial_layers, axial_overlays, core_layout, settings`.  The
feature summary set `multi_assembly_core=true`, while the retained FactsPatch
(`plan_build_state.json`, valid facts envelope) set `model_scope=single_assembly`,
left assembly counts empty, and omitted localized-insert requirements.  The
task planner therefore used the catalog/layout family, while the assembler
read only Facts and requested `pin_map`.  The resulting first assembly issue
was the misleading `assembly.missing_patch` for `pin_map`.

The facts reviewer did execute twice (`plan_loop_additional_llm_calls=2`,
`review_count=1`).  Both calls requested `json_schema`, but raw artifacts
`facts_review_raw_000.json` and `_001.json` contain prose with a drafted JSON
object rather than schema-valid output.  The schema retry retained the same
evidence pack, but its result was still invalid, so the stage correctly ended
as `review_failed`; it did not identify the scope/localized-insert regression
as accepted findings.  No candidate hash or revision attempt was recorded.

The CLI log also shows three graph-level incremental attempts.  Earlier patch
generation transport failures restarted the workflow; the final attempt reused
the bad valid facts and generated its downstream family, so no owner-level
semantic progress occurred.  This is the reason Phase 1C introduces a
deterministic feature-to-Facts preflight, a persisted canonical scope, and
owner-hash no-progress accounting before assembly.
