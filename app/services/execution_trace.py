from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


@dataclass
class _StepContext:
    trace: "ExecutionTrace"
    name: str
    agent: str
    provider: str | None = None
    model: str | None = None
    _step: dict[str, Any] = field(init=False)
    _started_monotonic: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self._step = {
            "name": self.name,
            "agent": self.agent,
            "status": "success",
            "provider": _normalize_text(self.provider) or None,
            "model": _normalize_text(self.model) or None,
            "started_at": None,
            "ended_at": None,
            "latency_ms": None,
            "fallback_used": False,
            "fallback_reason": None,
            "skip_reason": None,
            "details": None,
        }

    def set_status(self, status: str) -> None:
        self._step["status"] = _normalize_text(status).lower() or "success"

    def set_skip(self, skip_reason: str) -> None:
        self._step["status"] = "skipped"
        self._step["skip_reason"] = _normalize_text(skip_reason) or "skipped"

    def set_fallback(self, fallback_reason: str) -> None:
        self._step["status"] = "fallback"
        self._step["fallback_used"] = True
        self._step["fallback_reason"] = _normalize_text(fallback_reason) or "fallback"

    def set_failed(self, details: dict[str, Any] | None = None) -> None:
        self._step["status"] = "failed"
        if details:
            self.set_details(details)

    def set_details(self, details: dict[str, Any] | None = None, **kwargs: Any) -> None:
        merged: dict[str, Any] = {}
        existing = self._step.get("details")
        if isinstance(existing, dict):
            merged.update(existing)
        if isinstance(details, dict):
            merged.update(details)
        merged.update({key: value for key, value in kwargs.items() if value is not None})
        self._step["details"] = merged or None

    def __enter__(self) -> "_StepContext":
        self._started_monotonic = time.time()
        self._step["started_at"] = _utc_now_iso()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ended_at = _utc_now_iso()
        latency_ms = int(max(0.0, (time.time() - self._started_monotonic) * 1000))
        self._step["ended_at"] = ended_at
        self._step["latency_ms"] = latency_ms
        status = _normalize_text(self._step.get("status")).lower() or "success"
        if exc_type is not None:
            if status == "fallback":
                self._step["fallback_used"] = True
                self._step["fallback_reason"] = (
                    _normalize_text(self._step.get("fallback_reason")) or str(exc) or "fallback"
                )
            else:
                self._step["status"] = "failed"
                self._step["details"] = {
                    **(self._step.get("details") if isinstance(self._step.get("details"), dict) else {}),
                    "error": str(exc),
                }
            self.trace._append_step(self._step)
            return False

        if status == "skipped" and not _normalize_text(self._step.get("skip_reason")):
            self._step["skip_reason"] = "skipped"
        if status == "fallback":
            self._step["fallback_used"] = True
            self._step["fallback_reason"] = (
                _normalize_text(self._step.get("fallback_reason")) or "fallback"
            )
        self.trace._append_step(self._step)
        return False


class ExecutionTrace:
    def __init__(self, request_id: str, schema_version: int = 1) -> None:
        self.request_id = _normalize_text(request_id) or f"trace-{int(time.time() * 1000)}"
        self.schema_version = schema_version
        self._message_understanding: dict[str, Any] = {}
        self._execution_plan: dict[str, Any] = {}
        self._agent_plan: dict[str, Any] = {}
        self._memory_resolution: dict[str, Any] = {}
        self._tool_calls: list[dict[str, Any]] = []
        self._steps: list[dict[str, Any]] = []
        self._guards: list[dict[str, Any]] = []
        self._writes: dict[str, Any] = {
            "requested": [],
            "applied": [],
        }
        self._final_basis: dict[str, Any] = {}
        self._legacy_fallback: dict[str, Any] = {
            "used": False,
            "reason": None,
        }

    def set_message_understanding(
        self,
        *,
        intent: Any,
        route_hint: Any,
        provider: Any,
        model: Any,
        fallback_used: bool,
        fallback_reason: str | None,
        latency_ms: int | None,
    ) -> None:
        self._message_understanding = {
            "intent": _normalize_text(intent).lower() or "unknown",
            "route_hint": _normalize_text(route_hint).upper() or "UNKNOWN",
            "provider": _normalize_text(provider) or "fallback",
            "model": _normalize_text(model) or None,
            "fallback_used": bool(fallback_used),
            "fallback_reason": _normalize_text(fallback_reason) or None,
            "latency_ms": latency_ms if latency_ms is not None else None,
        }

    def set_execution_plan(
        self,
        *,
        primary_route: Any,
        actionable: bool,
        step_names: list[str] | tuple[str, ...] | set[str],
        response_mode: Any = None,
        persistence_allowed: bool | None = None,
        graph_update_allowed: bool | None = None,
    ) -> None:
        normalized_step_names = [
            _normalize_text(item)
            for item in list(step_names)
            if _normalize_text(item)
        ]
        self._execution_plan = {
            "primary_route": _normalize_text(primary_route).upper() or "UNKNOWN",
            "actionable": bool(actionable),
            "step_names": normalized_step_names,
            "response_mode": _normalize_text(response_mode) or None,
            "persistence_allowed": persistence_allowed if persistence_allowed is not None else None,
            "graph_update_allowed": graph_update_allowed if graph_update_allowed is not None else None,
        }

    def set_memory_resolution(self, resolution: dict[str, Any] | None = None, **kwargs: Any) -> None:
        payload: dict[str, Any] = {}
        if isinstance(resolution, dict):
            payload.update(resolution)
        payload.update({key: value for key, value in kwargs.items() if value is not None})
        self._memory_resolution = payload

    def set_agent_plan(
        self,
        *,
        intent: Any,
        route: Any,
        response_style: Any,
        specialist: Any,
        plan_summary: Any,
        tool_names: list[str] | tuple[str, ...] | set[str],
        writes_requested: list[dict[str, Any]] | None = None,
    ) -> None:
        normalized_tool_names = [
            _normalize_text(item)
            for item in list(tool_names)
            if _normalize_text(item)
        ]
        self._agent_plan = {
            "intent": _normalize_text(intent).lower() or "unknown",
            "route": _normalize_text(route).upper() or "UNKNOWN",
            "response_style": _normalize_text(response_style) or None,
            "specialist": _normalize_text(specialist) or None,
            "plan_summary": _normalize_text(plan_summary) or None,
            "tool_names": normalized_tool_names,
            "writes_requested": list(writes_requested or []),
        }

    def add_tool_call(
        self,
        *,
        name: str,
        category: str,
        status: str,
        dispatch_mode: str,
        current_implementation: str | None = None,
        planner_callable: bool | None = None,
        migration_status: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
        side_effect: str | None = None,
        arguments_summary: str | None = None,
        result_summary: str | None = None,
        blocked_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._tool_calls.append(
            {
                "name": _normalize_text(name),
                "category": _normalize_text(category) or None,
                "status": _normalize_text(status).lower() or "planned",
                "dispatch_mode": _normalize_text(dispatch_mode) or None,
                "current_implementation": _normalize_text(current_implementation) or None,
                "planner_callable": (
                    bool(planner_callable) if planner_callable is not None else None
                ),
                "migration_status": _normalize_text(migration_status) or None,
                "input_summary": _normalize_text(input_summary) or None,
                "output_summary": _normalize_text(output_summary) or None,
                "latency_ms": latency_ms if latency_ms is not None else None,
                "error": _normalize_text(error) or None,
                "side_effect": _normalize_text(side_effect) or None,
                "arguments_summary": _normalize_text(arguments_summary) or None,
                "result_summary": _normalize_text(result_summary) or None,
                "blocked_reason": _normalize_text(blocked_reason) or None,
                "details": details or None,
            }
        )

    def set_writes(
        self,
        *,
        requested: list[dict[str, Any]] | None = None,
        applied: list[dict[str, Any]] | None = None,
    ) -> None:
        self._writes = {
            "requested": list(requested or []),
            "applied": list(applied or []),
        }

    def set_final_basis(
        self,
        *,
        summary: str,
        evidence: list[str] | None = None,
        source: str | None = None,
    ) -> None:
        self._final_basis = {
            "summary": _normalize_text(summary) or None,
            "evidence": [
                _normalize_text(item)
                for item in list(evidence or [])
                if _normalize_text(item)
            ],
            "source": _normalize_text(source) or None,
        }

    def add_step(
        self,
        *,
        name: str,
        agent: str,
        status: str,
        provider: str | None = None,
        model: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        latency_ms: int | None = None,
        fallback_used: bool = False,
        fallback_reason: str | None = None,
        skip_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        normalized_status = _normalize_text(status).lower() or "success"
        step = {
            "name": _normalize_text(name),
            "agent": _normalize_text(agent),
            "status": normalized_status,
            "provider": _normalize_text(provider) or None,
            "model": _normalize_text(model) or None,
            "started_at": started_at or _utc_now_iso(),
            "ended_at": ended_at or _utc_now_iso(),
            "latency_ms": latency_ms if latency_ms is not None else 0,
            "fallback_used": bool(fallback_used),
            "fallback_reason": _normalize_text(fallback_reason) or None,
            "skip_reason": _normalize_text(skip_reason) or None,
            "details": details or None,
        }
        if normalized_status == "skipped" and not step["skip_reason"]:
            step["skip_reason"] = "skipped"
        if normalized_status == "fallback":
            step["fallback_used"] = True
            if not step["fallback_reason"]:
                step["fallback_reason"] = "fallback"
        self._append_step(step)

    def add_guard(self, *, name: str, triggered: bool) -> None:
        normalized_name = _normalize_text(name)
        for item in self._guards:
            if item.get("name") == normalized_name:
                item["triggered"] = bool(triggered)
                return
        self._guards.append({"name": normalized_name, "triggered": bool(triggered)})

    def mark_legacy_fallback(self, *, used: bool, reason: str | None) -> None:
        self._legacy_fallback = {
            "used": bool(used),
            "reason": _normalize_text(reason) or None,
        }

    def finalize(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "message_understanding": self._message_understanding,
            "execution_plan": self._execution_plan,
            "agent_plan": dict(self._agent_plan),
            "memory_resolution": dict(self._memory_resolution),
            "tool_calls": list(self._tool_calls),
            "steps": list(self._steps),
            "guards_applied": list(self._guards),
            "writes": dict(self._writes),
            "final_basis": dict(self._final_basis),
            "legacy_fallback": dict(self._legacy_fallback),
        }

    def step_context(
        self,
        *,
        name: str,
        agent: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> _StepContext:
        return _StepContext(
            trace=self,
            name=name,
            agent=agent,
            provider=provider,
            model=model,
        )

    def _append_step(self, step: dict[str, Any]) -> None:
        cleaned = dict(step)
        cleaned["name"] = _normalize_text(cleaned.get("name"))
        cleaned["agent"] = _normalize_text(cleaned.get("agent"))
        cleaned["status"] = _normalize_text(cleaned.get("status")).lower() or "success"
        cleaned["provider"] = _normalize_text(cleaned.get("provider")) or None
        cleaned["model"] = _normalize_text(cleaned.get("model")) or None
        cleaned["fallback_reason"] = _normalize_text(cleaned.get("fallback_reason")) or None
        cleaned["skip_reason"] = _normalize_text(cleaned.get("skip_reason")) or None
        if cleaned["status"] == "skipped" and not cleaned["skip_reason"]:
            cleaned["skip_reason"] = "skipped"
        if cleaned["status"] == "fallback":
            cleaned["fallback_used"] = True
            if not cleaned["fallback_reason"]:
                cleaned["fallback_reason"] = "fallback"
        self._steps.append(cleaned)
