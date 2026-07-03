"""Export a knowledge index from Pydantic schema field metadata.

The index maps a dotted field path (e.g.
``SimulationPlan.model_spec.pin_cell.geometry.fuel_radius_cm``) to the static
knowledge attached via :func:`~openmc_agent.schemas.KnowledgeField` /
``json_schema_extra``: ``description``, ``concept_id``, ``doc_refs``,
``retrieval_queries`` and ``common_errors``.

Use cases:
* Inject concise field knowledge into an LLM system prompt.
* Seed a future retrieval / GraphRAG index keyed by ``concept_id`` / ``doc_refs``.
* Audit which fields still lack knowledge metadata.

The walker supports nested models and the common ``Optional[...]`` /
``list[...]`` / ``X | None`` annotations used in this project.  It expands the
same model under different parent paths (e.g. ``RunSettingsSpec`` under both
``model_spec.settings`` and ``execution_check.settings``) while guarding against
self-referential cycles.
"""

from __future__ import annotations

import types
import typing
from typing import Any, TypeAlias

from pydantic import BaseModel

Index: TypeAlias = dict[str, dict[str, Any]]

_KNOWLEDGE_KEYS: tuple[str, ...] = (
    "concept_id",
    "doc_refs",
    "retrieval_queries",
    "common_errors",
)


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    """Return the BaseModel subclasses reachable from a type annotation."""
    origin = typing.get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return [annotation]
        return []

    if origin is types.UnionType or origin is typing.Union:
        models: list[type[BaseModel]] = []
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            models.extend(_model_types(arg))
        return models

    if origin in (list, set, frozenset) or origin is getattr(typing, "Sequence", None):
        models = []
        for arg in typing.get_args(annotation):
            models.extend(_model_types(arg))
        return models

    if origin is tuple:
        models = []
        for arg in typing.get_args(annotation):
            if arg is Ellipsis:
                continue
            models.extend(_model_types(arg))
        return models

    # dict / other generics: do not descend into scalar containers.
    return []


def _field_entry(field: Any) -> dict[str, Any]:
    """Extract the knowledge entry for a single Pydantic field."""
    entry: dict[str, Any] = {}
    if field.description:
        entry["description"] = field.description
    extra = field.json_schema_extra
    if isinstance(extra, dict):
        for key in _KNOWLEDGE_KEYS:
            if key in extra:
                entry[key] = extra[key]
    return entry


def export_schema_knowledge_index(
    model_cls: type[BaseModel],
    *,
    prefix: str | None = None,
    _chain: tuple[type[BaseModel], ...] | None = None,
) -> Index:
    """Recursively export field knowledge metadata for ``model_cls``.

    Parameters
    ----------
    model_cls:
        Root Pydantic model to walk.
    prefix:
        Dotted path prefix; defaults to ``model_cls.__name__`` so top-level keys
        are self-describing (e.g. ``SimulationPlan.model_spec...``).
    """
    if prefix is None:
        prefix = model_cls.__name__
    chain: tuple[type[BaseModel], ...] = (_chain or ()) + (model_cls,)

    index: Index = {}
    for name, field in model_cls.model_fields.items():
        path = f"{prefix}.{name}"
        entry = _field_entry(field)
        if entry:
            index[path] = entry
        for sub_model in _model_types(field.annotation):
            if sub_model in chain:
                continue  # break self-referential cycles
            index.update(
                export_schema_knowledge_index(sub_model, prefix=path, _chain=chain)
            )
    return index


__all__ = ["export_schema_knowledge_index"]
