# Grep Search Strategy

## Role

The grep search layer turns structured diagnostic issues into bounded local
evidence. It is used to locate relevant schema fields, renderer logic, tests,
examples, project docs, and optional local OpenMC docs. It does not execute
repairs and does not establish physical facts such as material density,
nuclide composition, benchmark constants, or cross section paths.

## Relationship To Validation

Validators, runtime parsers, and export checks emit `ValidationIssue` objects
with stable `code`, `schema_path`, `concept_id`, `grep_patterns`, `repair_hints`,
and `route_hint` fields. `openmc_agent.grep_search.grep_request_from_issue()`
converts those fields into a `GrepSearchRequest`.

The search result is converted with `grep_result_to_evidence()` into
`RetrievedEvidence(source_type="grep")`. Reflection prompts then receive:

- validation issue codes and paths;
- grep evidence locators and snippets;
- repair hints;
- explicit constraints against inventing unconfirmed physics or nuclear-data
  paths.

Legacy `ValidationReport.errors`, `warnings`, and `suggestions` remain
unchanged.

## Boundary With Future Graph And RAG

Grep is deterministic source localization. Future Graph and RAG layers should
consume the same `RetrievedEvidence` shape but keep their responsibilities
separate:

- grep match -> symbol or node resolver;
- issue code -> concept/error node;
- schema_path -> schema node;
- RAG -> cited document snippets after retrieval and ranking.

Grep evidence should not be treated as a final answer. It is prompt context for
repair, audit, and future graph expansion.

## Safety Strategy

The grep layer does not accept shell commands. Callers pass structured
patterns, globs, and roots. When `rg` is available, it is invoked through a
parameterized subprocess argument list; otherwise the implementation falls back
to Python `pathlib` and `re`.

Search roots are constrained to the project workspace and `/tmp` for tests.
Default roots are used only when present:

- `openmc_agent/`
- `tests/`
- `examples/`
- `docs/`
- `openmc_docs/`

Default include globs are text-oriented:

- `*.py`
- `*.md`
- `*.rst`
- `*.txt`
- `*.json`
- `*.yaml`
- `*.yml`
- `*.toml`

Default excludes cover VCS, virtualenvs, caches, bytecode, and OpenMC binary
outputs:

- `.git/**`
- `.venv/**`
- `venv/**`
- `__pycache__/**`
- `.pytest_cache/**`
- `*.pyc`
- `*.h5`
- `statepoint.*.h5`

The implementation also bounds `max_matches`, `context_lines`, per-file bytes,
total output characters, and pattern count.

## Current Triggers

Automatic grep evidence can be collected for:

- runtime issues;
- export_xml issues;
- hex lattice issues;
- validation issues with `grep_patterns` and repair/retrieval/manual routes;
- expert-feedback derived requests when converted into explicit
  `GrepSearchRequest` objects.

`ask_expert` issues such as missing cross section paths are not used as LLM
repair evidence for inventing values. If they later grep schema or docs, that
evidence must remain locator context only.

## Future Graph Integration

The intended next step is a resolver layer:

- parse each grep locator into a candidate file/symbol node;
- connect `issue.code` to an error/concept node;
- connect `schema_path` to a schema node;
- attach `RetrievedEvidence` as auditable edge metadata;
- let RAG add cited document nodes without replacing deterministic grep.
