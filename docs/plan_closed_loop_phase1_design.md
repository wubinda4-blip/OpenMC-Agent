# P2 Plan Closed-Loop Phase 1 Design Record

Phase 1 reuses the Phase 0 `PlanBuildState` ledgers, deterministic transition
controller, gate registry, action policy, and JSON artifact directory.  It does not
introduce a reviewer database or a second executor.  The facts gate is called directly
after the existing facts patch generator has parsed and validated `FactsPatch`, before
benchmark/reference metadata is synchronized and before downstream patch generation.

Evidence is sourced only from the resolved requirement and is represented by stable
line-range excerpts.  The independent critic returns a deliberately untrusted draft;
Python checks evidence hashes and facts-only paths, creates typed findings, and chooses
the action deterministically.  Advisory records a real review as `reviewed` and never
mutates the patch.  Controlled presently enables only facts and requires deterministic
approval before materials may be generated.

Contract 0.2 migrates only a legacy 0.1 `facts` stage marked
`review_not_implemented` back to `pending`.  Other foundation-only gates remain
skipped.  Revision and human confirmation remain facts-scoped extensions of this same
state and evidence protocol; no renderer/runtime loop is involved.
