# P2 Plan Closed-Loop Phase 0 Design Record

`PlanBuildState` is already the durable JSON state used by incremental planning.  Valid
and invalid patches are represented by `PlanPatchEnvelope.status`; plan-level repair
invalidates selected types plus their dependents and clears stale assembly.  Existing
validation repair keeps separate fingerprint/candidate ledgers and evaluates candidate
edits on a clone before committing.

Phase 0 extends that state with namespaced closed-loop ledgers rather than introducing a
second database or graph state machine.  The new package is reactor-neutral and has no
OpenMC dependency.  GraphState and inspect already pass configuration into the incremental
executor, so the same path carries a policy and outcome without adding graph nodes.

The executor remains authoritative for task order, retries, validation and assembly.  Off
does not enter the new framework.  Advisory only initializes persisted stages and artifacts;
it invokes no reviewer, repairer, supervisor, or interrupt.  Controlled returns an explicit
not-implemented configuration result until a later phase supplies a real gate.
