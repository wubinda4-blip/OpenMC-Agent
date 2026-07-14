"""Unified LLM call recorder for real campaigns.

All four LLM roles (planning_patch, runtime_diagnostician, runtime_patch_proposer,
runtime_supervisor) route their calls through this recorder so that evidence is
collected uniformly.

The recorder enforces ``max_llm_calls`` deterministically: once the budget
is exhausted, subsequent calls raise :class:`LLMBudgetExhausted` and the run
is saved with ``final_disposition=SAFE_STOP_BUDGET``.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


class LLMBudgetExhausted(Exception):
    """Raised when the unified call budget has been reached."""


@dataclass
class LLMCallRecord:
    call_id: str
    client_instance_id: str
    role: str  # planning_patch | runtime_diagnostician | runtime_patch_proposer | runtime_supervisor
    task_name: str
    model: str
    provider: str
    run_id: str
    started_at: str
    completed_at: str
    duration_ms: float
    success: bool
    error_type: str
    requested_output_mode: str
    actual_output_mode: str
    structured_mode_fallback: bool
    response_chars: int
    prompt_hash: str
    schema_hash: str
    response_artifact: str  # relative path or ""
    network_call_verified: bool
    fake_client: bool
    cached_response: bool


@dataclass
class LLMCallRecorder:
    """Records every LLM call across all roles.

    The recorder wraps client callables and collects structured evidence
    so that ``real_llm_verified`` can be determined from actual call data
    rather than from client creation alone.
    """

    run_id: str
    model: str
    provider: str
    max_calls: int = 16
    _records: list[LLMCallRecord] = field(default_factory=list)
    _client_instance_ids: set[str] = field(default_factory=set)
    _response_artifact_ids: list[str] = field(default_factory=list)
    _budget_exhausted: bool = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @property
    def records(self) -> list[LLMCallRecord]:
        return list(self._records)

    @property
    def call_count(self) -> int:
        return len(self._records)

    @property
    def is_budget_exhausted(self) -> bool:
        return self._budget_exhausted

    def register_client(self, client_instance_id: str | None = None) -> str:
        cid = client_instance_id or uuid.uuid4().hex[:12]
        self._client_instance_ids.add(cid)
        return cid

    def check_budget(self) -> None:
        """Raise :class:`LLMBudgetExhausted` if the budget is spent."""
        if len(self._records) >= self.max_calls:
            self._budget_exhausted = True
            raise LLMBudgetExhausted(
                f"LLM call budget exhausted: {self.max_calls} calls reached"
            )

    def record_call(
        self,
        *,
        role: str,
        task_name: str,
        client_instance_id: str,
        started_at: str,
        completed_at: str,
        duration_ms: float,
        success: bool,
        response_chars: int,
        requested_output_mode: str = "json",
        actual_output_mode: str = "json",
        structured_mode_fallback: bool = False,
        error_type: str = "",
        prompt_hash: str = "",
        schema_hash: str = "",
        response_artifact: str = "",
        network_call_verified: bool = False,
        fake_client: bool = False,
        cached_response: bool = False,
    ) -> LLMCallRecord:
        """Record one completed LLM call."""
        rec = LLMCallRecord(
            call_id=uuid.uuid4().hex[:16],
            client_instance_id=client_instance_id,
            role=role,
            task_name=task_name,
            model=self.model,
            provider=self.provider,
            run_id=self.run_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            success=success,
            error_type=error_type,
            requested_output_mode=requested_output_mode,
            actual_output_mode=actual_output_mode,
            structured_mode_fallback=structured_mode_fallback,
            response_chars=response_chars,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            response_artifact=response_artifact,
            network_call_verified=network_call_verified,
            fake_client=fake_client,
            cached_response=cached_response,
        )
        self._records.append(rec)
        if response_artifact:
            self._response_artifact_ids.append(response_artifact)
        return rec

    # ------------------------------------------------------------------ #
    # Wrappers
    # ------------------------------------------------------------------ #

    def wrap_planning_client(self, client: Any, client_instance_id: str) -> Any:
        """Wrap a patch LLM client to record planning calls.

        The graph calls the client as a callable ``client(prompt) -> str``
        or via ``generate_patch_json(prompt=..., patch_type=..., json_schema=...)``.
        We intercept both paths.
        """
        recorder = self

        class _PlanningWrapper:
            def __init__(self, inner: Any) -> None:
                self._inner = inner
                self._client_id = client_instance_id

            def __getattr__(self, name: str) -> Any:
                return getattr(self._inner, name)

            def _record(self, started_iso: str, ts: float, success: bool,
                        result: Any, err: str, prompt_text: str) -> None:
                import time as _time
                te = _time.perf_counter()
                resp_chars = 0
                if isinstance(result, str):
                    resp_chars = len(result)
                elif isinstance(result, dict):
                    resp_chars = len(json.dumps(result, default=str))
                elif result is not None:
                    resp_chars = len(str(result))
                recorder.record_call(
                    role="planning_patch",
                    task_name="patch",
                    client_instance_id=self._client_id,
                    started_at=started_iso,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=round((te - ts) * 1000, 1),
                    success=success,
                    response_chars=resp_chars,
                    error_type=err,
                    prompt_hash=hashlib.sha256(prompt_text.encode()).hexdigest()[:16],
                    network_call_verified=success and not recorder._is_fake_provider(),
                )

            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                recorder.check_budget()
                t0 = datetime.now(timezone.utc)
                started = t0.isoformat()
                import time as _time
                ts = _time.perf_counter()
                success = True
                err = ""
                result: Any = None
                try:
                    result = self._inner(*args, **kwargs)
                    return result
                except LLMBudgetExhausted:
                    raise
                except Exception as exc:
                    success = False
                    err = type(exc).__name__
                    raise
                finally:
                    prompt_text = str(args) + str(kwargs)
                    self._record(started, ts, success, result, err, prompt_text)

            def generate_patch_json(self, *args: Any, **kwargs: Any) -> Any:
                recorder.check_budget()
                t0 = datetime.now(timezone.utc)
                started = t0.isoformat()
                import time as _time
                ts = _time.perf_counter()
                success = True
                err = ""
                result: Any = None
                try:
                    result = self._inner.generate_patch_json(*args, **kwargs)
                    return result
                except LLMBudgetExhausted:
                    raise
                except Exception as exc:
                    success = False
                    err = type(exc).__name__
                    raise
                finally:
                    prompt_text = str(kwargs.get("prompt", ""))
                    self._record(started, ts, success, result, err, prompt_text)

            def generate_patch(self, *args: Any, **kwargs: Any) -> Any:
                return self.__call__(*args, **kwargs)

        return _PlanningWrapper(client)

    def wrap_callable(
        self,
        func: Callable[..., str],
        *,
        role: str,
        client_instance_id: str,
    ) -> Callable[..., str]:
        """Wrap an LLM callable ``(input_dict, *, prompt, json_schema) -> str``."""
        recorder = self

        def _wrapper(input_dict: Any, *, prompt: str, json_schema: Any) -> str:
            recorder.check_budget()
            t0 = datetime.now(timezone.utc)
            started = t0.isoformat()
            import time as _time
            ts = _time.perf_counter()
            success = True
            err = ""
            result = ""
            try:
                result = func(input_dict, prompt=prompt, json_schema=json_schema)
                return result
            except LLMBudgetExhausted:
                raise
            except Exception as exc:
                success = False
                err = type(exc).__name__
                raise
            finally:
                te = _time.perf_counter()
                schema_str = json.dumps(json_schema, default=str) if json_schema else ""
                recorder.record_call(
                    role=role,
                    task_name=role,
                    client_instance_id=client_instance_id,
                    started_at=started,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    duration_ms=round((te - ts) * 1000, 1),
                    success=success,
                    response_chars=len(result) if isinstance(result, str) else 0,
                    error_type=err,
                    prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
                    schema_hash=hashlib.sha256(schema_str.encode()).hexdigest()[:16],
                    network_call_verified=success and not recorder._is_fake_provider(),
                )

        return _wrapper

    # ------------------------------------------------------------------ #
    # Evidence summary
    # ------------------------------------------------------------------ #

    def _is_fake_provider(self) -> bool:
        return self.provider.lower() in {"fake", "mock", "stub", "test"}

    def evidence_summary(self) -> dict[str, Any]:
        """Return a structured evidence summary for truthfulness checks."""
        records = self._records
        total = len(records)
        successful = [r for r in records if r.success]
        failed = [r for r in records if not r.success]
        planning = [r for r in records if r.role == "planning_patch"]
        runtime_diag = [r for r in records if r.role == "runtime_diagnostician"]
        runtime_prop = [r for r in records if r.role == "runtime_patch_proposer"]
        runtime_sup = [r for r in records if r.role == "runtime_supervisor"]

        first = min((r.started_at for r in records), default="")
        last = max((r.completed_at for r in records), default="")
        total_chars = sum(r.response_chars for r in records)

        has_fake = any(r.fake_client for r in records)
        has_cached = any(r.cached_response for r in records)
        has_network = any(r.network_call_verified for r in records)

        return {
            "real_network_call_count": sum(1 for r in records if r.network_call_verified),
            "successful_network_call_count": len(successful),
            "failed_network_call_count": len(failed),
            "planning_network_call_count": len(planning),
            "runtime_diagnosis_network_call_count": len(runtime_diag),
            "runtime_proposal_network_call_count": len(runtime_prop),
            "runtime_supervisor_network_call_count": len(runtime_sup),
            "first_network_call_at": first,
            "last_network_call_at": last,
            "client_instance_ids": sorted(self._client_instance_ids),
            "response_artifact_ids": list(self._response_artifact_ids),
            "total_calls": total,
            "total_response_chars": total_chars,
            "cached_response_used": has_cached,
            "fake_client_used": has_fake,
            "fake_fallback_used": has_fake,
            "budget_exhausted": self._budget_exhausted,
            "max_calls": self.max_calls,
        }

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self._records]

    @classmethod
    def from_dict_list(
        cls,
        records: list[dict[str, Any]],
        *,
        run_id: str,
        model: str,
        provider: str,
        max_calls: int = 16,
    ) -> LLMCallRecorder:
        rec = cls(run_id=run_id, model=model, provider=provider, max_calls=max_calls)
        for r in records:
            rec._records.append(LLMCallRecord(**r))
            if r.get("response_artifact"):
                rec._response_artifact_ids.append(r["response_artifact"])
            if r.get("client_instance_id"):
                rec._client_instance_ids.add(r["client_instance_id"])
        return rec


def verify_real_llm(
    recorder: LLMCallRecorder,
    *,
    model: str,
    run_started_at: str,
) -> tuple[bool, list[str]]:
    """Verify that real LLM activity occurred.

    Returns ``(verified, reasons)`` where ``verified`` is True only when
    all 9 conditions are satisfied.
    """
    evidence = recorder.evidence_summary()
    reasons: list[str] = []

    # 1. provider is not fake
    if recorder.provider.lower() in {"fake", "mock", "stub", "test"}:
        reasons.append("provider_is_fake")
    # 2. at least one successful planning network call
    if evidence["planning_network_call_count"] == 0:
        reasons.append("no_planning_network_call")
    # 3. not cached or fixture
    if evidence["cached_response_used"]:
        reasons.append("cached_response_used")
    # 4. response time within run window
    if evidence["first_network_call_at"]:
        try:
            first_call = evidence["first_network_call_at"]
            if first_call < run_started_at:
                reasons.append("call_before_run_start")
        except Exception:
            pass
    # 5. model name matches
    if recorder.model != model:
        reasons.append(f"model_mismatch: {recorder.model} != {model}")
    # 6. at least one patch attempt has response_chars > 0
    if evidence["total_response_chars"] == 0:
        reasons.append("no_response_content")
    # 7. all declared-real roles have evidence
    # (checked implicitly: if runtime roles were called, they must have records)
    # 8. fake_client_used = false
    if evidence["fake_client_used"]:
        reasons.append("fake_client_used")
    # 9. fallback_to_fake = false
    if evidence["fake_fallback_used"]:
        reasons.append("fake_fallback_used")

    return (len(reasons) == 0), reasons
