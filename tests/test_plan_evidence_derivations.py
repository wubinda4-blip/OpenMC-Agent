"""Tests for deterministic derivations: integer_product, integer_sum,
matrix_shape, count_by_label, equality_alias, interval_length.

Includes cycle detection, missing-input rejection, and result re-verification.
Also includes a reactor-neutral example (3x3 core layout).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openmc_agent.plan_investigation.errors import PlanInvestigationIssue
from openmc_agent.plan_investigation.evidence_ledger import (
    add_claim,
    add_derivation,
    add_derived_claim,
    create_empty_ledger,
    find_stale_derived_claims,
    recompute_derivation,
)
from openmc_agent.plan_investigation.hashing import content_hash
from openmc_agent.plan_investigation.models import (
    EvidenceClaim,
    EvidenceDerivation,
    EvidenceSourceRef,
    EvidenceStatus,
    SourceKind,
)
from openmc_agent.plan_investigation.source_index import build_source_index


# Pydantic v2 wraps ValueError raised inside validators as ValidationError.
_MODEL_FAILURE = (ValidationError, PlanInvestigationIssue)


def _idx(text="alpha\nbeta\n"):
    idx = build_source_index(text=text, title="t", source_kind=SourceKind.USER_REQUIREMENT)
    span = idx.make_span(1, 1)
    idx.register_span(span)
    return idx, span


def _explicit_claim(value, *, subject="x", predicate="p", source_id=None, span_id=None, excerpt_hash=None):
    return EvidenceClaim(
        claim_id="",
        subject=subject,
        predicate=predicate,
        value=value,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=source_id, span_id=span_id, excerpt_hash=excerpt_hash),),
    )


# ---------------------------------------------------------------------------
# Each operation
# ---------------------------------------------------------------------------


def test_integer_product() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(3, subject="grid", predicate="rows", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    b = _explicit_claim(4, subject="grid", predicate="cols", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    add_claim(ld, b, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_product",
        input_claim_ids=(a.claim_id, b.claim_id),
        parameters={},
        result_hash=content_hash(12),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [3, 4]) == 12


def test_integer_sum() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(10, source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    b = _explicit_claim(20, subject="x", predicate="b", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    add_claim(ld, b, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=(a.claim_id, b.claim_id),
        parameters={},
        result_hash=content_hash(30),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [10, 20]) == 30


def test_matrix_shape() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    layout = _explicit_claim(
        [[1, 2, 3], [4, 5, 6]],
        subject="grid",
        predicate="data",
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    add_claim(ld, layout, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="matrix_shape",
        input_claim_ids=(layout.claim_id,),
        parameters={},
        result_hash=content_hash([2, 3]),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [[[1, 2, 3], [4, 5, 6]]]) == [2, 3]


def test_count_by_label_flat() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    labels = _explicit_claim(
        ["C", "E", "C", "R"],
        subject="grid",
        predicate="labels",
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    add_claim(ld, labels, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="count_by_label",
        input_claim_ids=(labels.claim_id,),
        parameters={"only": ["C", "R"]},
        result_hash=content_hash({"C": 2, "R": 1}),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [["C", "E", "C", "R"]]) == {"C": 2, "R": 1}


def test_count_by_label_nested_grid() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    grid = _explicit_claim(
        [["C", "E", "C"], ["E", "R", "E"], ["C", "E", "C"]],
        subject="core",
        predicate="layout",
        source_id=idx.document.source_id,
        span_id=span.span_id,
        excerpt_hash=span.excerpt_hash,
    )
    add_claim(ld, grid, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="count_by_label",
        input_claim_ids=(grid.claim_id,),
        parameters={"only": ["C", "E", "R"]},
        result_hash=content_hash({"C": 4, "E": 4, "R": 1}),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [[["C", "E", "C"], ["E", "R", "E"], ["C", "E", "C"]]]) == {
        "C": 4,
        "E": 4,
        "R": 1,
    }


def test_equality_alias() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(7, subject="x", predicate="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    b = _explicit_claim(7, subject="x", predicate="b", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    add_claim(ld, b, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="equality_alias",
        input_claim_ids=(a.claim_id, b.claim_id),
        parameters={},
        result_hash=content_hash(7),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [7, 7]) == 7


def test_equality_alias_rejects_disagreeing_inputs() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(7, subject="x", predicate="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    b = _explicit_claim(8, subject="x", predicate="b", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    add_claim(ld, b, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="equality_alias",
        input_claim_ids=(a.claim_id, b.claim_id),
        parameters={},
        result_hash=content_hash(7),
    )
    with pytest.raises(PlanInvestigationIssue):
        add_derivation(ld, der)


def test_interval_length_list_form() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    interval = _explicit_claim([10, 25], subject="axial", predicate="range", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, interval, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="interval_length",
        input_claim_ids=(interval.claim_id,),
        parameters={},
        result_hash=content_hash(15),
    )
    add_derivation(ld, der)
    assert recompute_derivation(der, [[10, 25]]) == 15


def test_interval_length_dict_form() -> None:
    der = EvidenceDerivation(
        derivation_id="",
        operation="interval_length",
        input_claim_ids=("claim_x",),
        parameters={},
        result_hash=content_hash(20),
    )
    assert recompute_derivation(der, [{"lo": 0, "hi": 20}]) == 20


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_derived_claim_with_missing_input_rejected() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=("claim_missing",),
        parameters={},
        result_hash=content_hash(0),
    )
    with pytest.raises(PlanInvestigationIssue):
        add_derivation(ld, der)


def test_derivation_result_mismatch_rejected() -> None:
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(3, subject="x", predicate="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    b = _explicit_claim(4, subject="x", predicate="b", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    add_claim(ld, b, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_product",
        input_claim_ids=(a.claim_id, b.claim_id),
        parameters={},
        result_hash=content_hash(999),  # wrong!
    )
    with pytest.raises(PlanInvestigationIssue):
        add_derivation(ld, der)


def test_unknown_operation_rejected() -> None:
    with pytest.raises(_MODEL_FAILURE):
        EvidenceDerivation(
            derivation_id="",
            operation="eval",  # not in allow-list
            input_claim_ids=("claim_a",),
            parameters={},
            result_hash="x",
        )


def test_no_eval_or_exec_in_operations() -> None:
    """The allow-list must never permit arbitrary code execution."""
    from openmc_agent.plan_investigation.models import ALLOWED_DERIVATION_OPERATIONS

    forbidden = {"eval", "exec", "compile", "import", "subprocess", "system"}
    assert not (forbidden & ALLOWED_DERIVATION_OPERATIONS)


def test_cycle_detection_chained() -> None:
    """Cycle detection guards against tampered-resume scenarios where a
    claim's ``derivation`` field has been hand-edited to point at a
    derivation whose inputs eventually cycle back.

    Construct: A (explicit) -> B (derived) -> [candidate] A again.  The
    candidate derivation is rejected because walking its input claim's
    upstream derivation returns to the candidate itself.
    """
    idx, span = _idx()
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(2, subject="x", predicate="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})

    # Build derived B from A.
    der_b = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=(a.claim_id,),
        parameters={"operands": [1]},
        result_hash=content_hash(3),
    )
    b = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="b",
        value=3,
        status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
        derivation=der_b,
    )
    add_derived_claim(ld, claim=b, derivation=der_b)

    # Hand-tamper: re-point A's derivation field at a NEW derivation whose
    # only input is B.  This creates A -> B -> A.  We bypass model
    # revalidation via object.__setattr__ to simulate a corrupted resume.
    der_a_loop = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=(b.claim_id,),
        parameters={"operands": [-1]},
        result_hash=content_hash(2),
    )
    tampered_a = a.model_copy(update={"derivation": der_a_loop})
    # Suppress Pydantic revalidation by direct attribute set, then put the
    # tampered claim back into the ledger.
    object.__setattr__(tampered_a, "derivation", der_a_loop)
    ld.claims[a.claim_id] = tampered_a
    ld.derivations[der_a_loop.derivation_id] = der_a_loop

    # Now registering ANY new derivation that pulls in B (or A) must detect
    # the cycle.
    der_outside = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=(b.claim_id,),
        parameters={"operands": [10]},
        result_hash=content_hash(13),
    )
    with pytest.raises(PlanInvestigationIssue):
        add_derivation(ld, der_outside)


def test_stale_derived_claim_detected_after_input_removed() -> None:
    """When a derived claim's input is removed (re-built ledger), it's stale."""
    idx, span = _idx()
    # Build the original ledger with a derived claim.
    ld = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    a = _explicit_claim(3, subject="x", predicate="a", source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash)
    add_claim(ld, a, source_indexes={idx.document.source_id: idx})
    der = EvidenceDerivation(
        derivation_id="",
        operation="integer_sum",
        input_claim_ids=(a.claim_id,),
        parameters={"operands": [1]},
        result_hash=content_hash(4),
    )
    b = EvidenceClaim(
        claim_id="",
        subject="x",
        predicate="b",
        value=4,
        status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
        derivation=der,
    )
    add_derived_claim(ld, claim=b, derivation=der)
    assert find_stale_derived_claims(ld) == []

    # Simulate a resume scenario: rebuild the ledger WITHOUT claim a.
    ld2 = create_empty_ledger(requirement_hash="rh", source_indexes=[idx])
    # Re-add just the derivation (b is "carried over" but its input is gone).
    ld2.derivations[der.derivation_id] = der
    ld2.claims[b.claim_id] = b
    stale = find_stale_derived_claims(ld2)
    assert b.claim_id in stale


# ---------------------------------------------------------------------------
# Reactor-neutral end-to-end example (NOT VERA-specific)
# ---------------------------------------------------------------------------


def test_reactor_neutral_3x3_core_example() -> None:
    """End-to-end: a 3x3 core layout described in a problem statement.

    Source text:
        The model represents a full 3 by 3 core.
        The layout is:

        C E C
        E R E
        C E C

    Verify:
      - explicit claim: core.layout = [[C,E,C],[E,R,E],[C,E,C]]
      - derived claim: matrix_shape = [3, 3]
      - derived claim: assembly_type_counts = {C:4, E:4, R:1}
      - derived claim: total_assembly_count = 9 (integer_product of shape)
      - all derivations re-compute to the stated result.
    """

    text = (
        "The model represents a full 3 by 3 core.\n"
        "The layout is:\n"
        "\n"
        "C E C\n"
        "E R E\n"
        "C E C\n"
    )
    idx = build_source_index(text=text, title="Demo core", source_kind=SourceKind.USER_REQUIREMENT)
    # Layout spans lines 4-6.
    span = idx.make_span(4, 6)
    idx.register_span(span)
    assert span.excerpt == "C E C\nE R E\nC E C"

    layout_value = [["C", "E", "C"], ["E", "R", "E"], ["C", "E", "C"]]
    layout_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="layout",
        value=layout_value,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
    )

    ld = create_empty_ledger(requirement_hash=content_hash(text), source_indexes=[idx])
    add_claim(ld, layout_claim, source_indexes={idx.document.source_id: idx})

    # Explicit integer claims that decompose the layout shape, citing the
    # same span.  These give integer_product inputs for total count.
    rows_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="row_count",
        value=3,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
    )
    cols_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="col_count",
        value=3,
        status=EvidenceStatus.EXPLICIT,
        source_refs=(EvidenceSourceRef(source_id=idx.document.source_id, span_id=span.span_id, excerpt_hash=span.excerpt_hash),),
    )
    add_claim(ld, rows_claim, source_indexes={idx.document.source_id: idx})
    add_claim(ld, cols_claim, source_indexes={idx.document.source_id: idx})

    # Derived: matrix_shape from layout.
    der_shape = EvidenceDerivation(
        derivation_id="",
        operation="matrix_shape",
        input_claim_ids=(layout_claim.claim_id,),
        parameters={},
        result_hash=content_hash([3, 3]),
    )
    shape_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="matrix_shape",
        value=[3, 3],
        status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
        derivation=der_shape,
    )
    add_derived_claim(ld, claim=shape_claim, derivation=der_shape)
    assert shape_claim.value == [3, 3]

    # Derived: count_by_label from layout.
    der_counts = EvidenceDerivation(
        derivation_id="",
        operation="count_by_label",
        input_claim_ids=(layout_claim.claim_id,),
        parameters={"only": ["C", "E", "R"]},
        result_hash=content_hash({"C": 4, "E": 4, "R": 1}),
    )
    counts_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="assembly_type_counts",
        value={"C": 4, "E": 4, "R": 1},
        status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
        derivation=der_counts,
    )
    add_derived_claim(ld, claim=counts_claim, derivation=der_counts)
    assert counts_claim.value == {"C": 4, "E": 4, "R": 1}

    # Derived: total_assembly_count via integer_product of rows * cols.
    der_total = EvidenceDerivation(
        derivation_id="",
        operation="integer_product",
        input_claim_ids=(rows_claim.claim_id, cols_claim.claim_id),
        parameters={},
        result_hash=content_hash(9),
    )
    total_claim = EvidenceClaim(
        claim_id="",
        subject="core",
        predicate="assembly_count",
        value=9,
        status=EvidenceStatus.DETERMINISTICALLY_DERIVED,
        derivation=der_total,
    )
    add_derived_claim(ld, claim=total_claim, derivation=der_total)
    assert total_claim.value == 9

    # All three derived claims should be present and not stale.
    assert shape_claim.claim_id in ld.claims
    assert counts_claim.claim_id in ld.claims
    assert total_claim.claim_id in ld.claims
    assert find_stale_derived_claims(ld) == []
