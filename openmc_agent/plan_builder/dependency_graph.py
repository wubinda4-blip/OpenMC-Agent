"""Single typed dependency graph for incremental plan patches.

This replaces ad-hoc dependency expansion with a stable, reactor-neutral
graph.  Whether a node is required remains the canonical task-plan's job;
this module only describes relationships and never forces optional nodes.
"""

from __future__ import annotations

from collections import defaultdict, deque

from openmc_agent.plan_builder.closed_loop.models import PlanGateId
from openmc_agent.plan_builder.closed_loop.policy import gate_for_patch_type


class PlanPatchDependencyGraph:
    _ORDER: tuple[str, ...] = (
        "facts", "materials", "universes", "localized_insert_profiles",
        "base_path_axial_profiles", "pin_map", "assembly_catalog",
        "axial_layers", "axial_overlays", "core_layout", "settings",
    )
    _DEPENDENCIES: dict[str, tuple[str, ...]] = {
        "facts": (),
        "materials": ("facts",),
        "universes": ("facts", "materials"),
        "localized_insert_profiles": ("facts", "universes"),
        "base_path_axial_profiles": ("facts", "universes"),
        "pin_map": ("facts", "universes", "localized_insert_profiles"),
        "assembly_catalog": ("facts", "universes", "localized_insert_profiles"),
        "axial_layers": ("facts", "pin_map", "assembly_catalog"),
        "axial_overlays": ("facts", "materials", "axial_layers", "assembly_catalog"),
        "core_layout": ("facts", "assembly_catalog"),
        "settings": ("facts",),
    }

    def dependencies_of(self, patch_type: str) -> list[str]:
        return list(self._DEPENDENCIES.get(patch_type, ()))

    def dependents_of(self, patch_type: str) -> list[str]:
        return [node for node in self._ORDER if patch_type in self._DEPENDENCIES.get(node, ())]

    def transitive_dependents(self, patch_types: list[str]) -> list[str]:
        wanted = set(patch_types)
        queue = deque(patch_types)
        while queue:
            current = queue.popleft()
            for dependent in self.dependents_of(current):
                if dependent not in wanted:
                    wanted.add(dependent)
                    queue.append(dependent)
        return [node for node in self._ORDER if node in wanted]

    def topological_order(self, patch_types: list[str]) -> list[str]:
        selected = set(patch_types)
        indegree = {node: len([dep for dep in self.dependencies_of(node) if dep in selected]) for node in selected}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for node in selected:
            for dependency in self.dependencies_of(node):
                if dependency in selected:
                    outgoing[dependency].append(node)
        order_index = {node: index for index, node in enumerate(self._ORDER)}
        ready = sorted((node for node, degree in indegree.items() if degree == 0), key=order_index.__getitem__)
        ordered: list[str] = []
        while ready:
            node = ready.pop(0)
            ordered.append(node)
            for dependent in sorted(outgoing[node], key=order_index.__getitem__):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
                    ready.sort(key=order_index.__getitem__)
        if len(ordered) != len(selected):
            raise ValueError("plan patch dependency graph contains a cycle")
        return ordered

    def earliest_patch_type(self, patch_types: list[str]) -> str | None:
        ordered = self.topological_order([node for node in patch_types if node in self._DEPENDENCIES])
        return ordered[0] if ordered else None

    def gates_affected_by_patch_types(self, patch_types: list[str]) -> list[PlanGateId]:
        ordered = (PlanGateId.FACTS, PlanGateId.MATERIAL_UNIVERSE, PlanGateId.PLACEMENT, PlanGateId.AXIAL_GEOMETRY, PlanGateId.ASSEMBLED_PLAN)
        affected = {gate_for_patch_type(patch_type) for patch_type in patch_types}
        # A patch change always invalidates the assembled-plan conclusion.
        affected.add(PlanGateId.ASSEMBLED_PLAN)
        return [gate for gate in ordered if gate in affected]

    def validate_dependency_graph(self) -> None:
        unknown = set(self._DEPENDENCIES).difference(self._ORDER)
        if unknown:
            raise ValueError(f"unknown graph nodes: {sorted(unknown)}")
        for node, dependencies in self._DEPENDENCIES.items():
            missing = set(dependencies).difference(self._DEPENDENCIES)
            if missing:
                raise ValueError(f"{node} depends on unknown patches: {sorted(missing)}")
        self.topological_order(list(self._ORDER))


DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH = PlanPatchDependencyGraph()


def dependencies_of(patch_type: str) -> list[str]:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.dependencies_of(patch_type)


def dependents_of(patch_type: str) -> list[str]:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.dependents_of(patch_type)


def transitive_dependents(patch_types: list[str]) -> list[str]:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.transitive_dependents(patch_types)


def topological_order(patch_types: list[str]) -> list[str]:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.topological_order(patch_types)


def earliest_patch_type(patch_types: list[str]) -> str | None:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.earliest_patch_type(patch_types)


def gates_affected_by_patch_types(patch_types: list[str]) -> list[PlanGateId]:
    return DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.gates_affected_by_patch_types(patch_types)


def validate_dependency_graph() -> None:
    DEFAULT_PLAN_PATCH_DEPENDENCY_GRAPH.validate_dependency_graph()
