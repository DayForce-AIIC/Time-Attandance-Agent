"""Deterministic, offline proposal provider used by tests and demos."""

import json
from datetime import datetime, timedelta

from app.llm.base import LLMResult, PromptBundle
from app.schemas import Action, LLMProposal, ProposedCorrection, PunchKind


class MockClient:
    """Produce predictable rule-specific proposals without network access."""

    model = "mock"

    def generate(
        self, bundle: PromptBundle, attempt: int, feedback: str | None = None
    ) -> LLMResult:
        """Read the trusted prompt context and return schema-shaped JSON."""
        del attempt, feedback
        context = bundle.context
        exception = context["exception"]
        constraints = context["constraints"]
        schedule = context.get("schedule")
        if not isinstance(exception, dict) or not isinstance(constraints, dict):
            raise ValueError("mock provider requires structured exception and constraints")
        if schedule is not None and not isinstance(schedule, dict):
            raise ValueError("mock provider schedule context must be an object or null")

        rule_id = str(exception["rule_id"])
        correction = self._correction(rule_id, context, schedule, constraints)
        raw_policies = context.get("policies")
        if not isinstance(raw_policies, list):
            raise ValueError("mock provider requires a list of policy passages")
        policy_passages = []
        for passage in raw_policies:
            if not isinstance(passage, dict):
                raise ValueError("mock provider requires policy passage objects")
            policy_passages.append(passage)
        cited_ids = [str(passage["policy_id"]) for passage in policy_passages]
        policy_ids = [rule_id]
        policy_ids.extend(policy_id for policy_id in cited_ids if policy_id != rule_id)
        explanation = (
            f"Rule {rule_id} is supported by the timecard evidence. "
            "This bounded proposal is ready for manager review."
        )
        proposal = LLMProposal(
            exception_id=bundle.exception_id,
            explanation=explanation,
            proposed_correction=correction,
            policy_ids_cited=policy_ids[:2],
            confidence=0.92,
        )
        return LLMResult(
            raw_text=json.dumps(proposal.model_dump(mode="json"), separators=(",", ":")),
            latency_ms=10,
            prompt_tokens=None,
            completion_tokens=None,
            model=self.model,
        )

    def _correction(
        self,
        rule_id: str,
        context: dict[str, object],
        schedule: dict[str, object] | None,
        constraints: dict[str, object],
    ) -> ProposedCorrection:
        """Choose the fixed mock action for one supported detector rule."""
        window = constraints.get("ts_window")
        if not isinstance(window, dict):
            window = None
        earliest = str(window["earliest"]) if window else None
        latest = str(window["latest"]) if window else None

        if rule_id == "PUNCH-01":
            candidate = self._schedule_time(schedule, context, "end")
            return self._add_punch(PunchKind.OUT, self._clamp(candidate, earliest, latest))
        if rule_id == "PUNCH-02":
            candidate = self._schedule_time(schedule, context, "start") or earliest
            return self._add_punch(PunchKind.IN, self._clamp(candidate, earliest, latest))
        if rule_id == "PUNCH-03":
            punch_id = self._first_target_id(constraints.get("target_punch_ids", []))
            if punch_id is None:
                return ProposedCorrection(action=Action.ESCALATE)
            return ProposedCorrection(action=Action.REMOVE_PUNCH, punch_id=punch_id)
        if rule_id == "PUNCH-04":
            exception_context = context.get("exception")
            evidence = (
                exception_context.get("evidence", {}) if isinstance(exception_context, dict) else {}
            )
            break_start = evidence.get("break_start_ts") if isinstance(evidence, dict) else None
            candidate = self._add_minutes(str(break_start), 30) if break_start else earliest
            return self._add_punch(PunchKind.BREAK_END, self._clamp(candidate, earliest, latest))
        if rule_id == "MAX-01":
            punch_id = self._first_target_id(constraints.get("target_punch_ids", []))
            if punch_id is None:
                return ProposedCorrection(action=Action.ESCALATE)
            candidate = self._schedule_time(schedule, context, "end") or earliest
            timestamp = self._clamp(candidate, earliest, latest)
            if timestamp is None:
                return ProposedCorrection(action=Action.ESCALATE)
            return ProposedCorrection(
                action=Action.MODIFY_PUNCH,
                punch_id=punch_id,
                ts=timestamp,
            )
        if rule_id == "OT-02":
            schedule_id = constraints.get("target_schedule_id")
            if isinstance(schedule_id, bool) or not isinstance(schedule_id, int):
                return ProposedCorrection(action=Action.FLAG_FOR_APPROVAL)
            candidate = self._overtime_schedule_end(schedule, constraints) or earliest
            timestamp = self._clamp(candidate, earliest, latest)
            if timestamp is None:
                return ProposedCorrection(action=Action.FLAG_FOR_APPROVAL)
            return ProposedCorrection(
                action=Action.ADJUST_SCHEDULE,
                schedule_id=schedule_id,
                ts=timestamp,
            )
        if rule_id == "PUNCH-99":
            return ProposedCorrection(action=Action.ESCALATE)
        return ProposedCorrection(action=Action.FLAG_FOR_APPROVAL)

    @staticmethod
    def _first_target_id(values: object) -> int | None:
        """Return the first integer target identifier from a JSON-shaped list."""
        if not isinstance(values, list) or not values:
            return None
        value = values[0]
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _add_punch(kind: PunchKind, timestamp: str | None) -> ProposedCorrection:
        if timestamp is None:
            return ProposedCorrection(action=Action.ESCALATE)
        return ProposedCorrection(action=Action.ADD_PUNCH, kind=kind, ts=timestamp)

    @staticmethod
    def _schedule_time(
        schedule: dict[str, object] | None,
        context: dict[str, object],
        key: str,
    ) -> str | None:
        if schedule is not None:
            value = schedule.get(key)
            if value is not None:
                return str(value)
        exception = context.get("exception")
        evidence = exception.get("evidence") if isinstance(exception, dict) else None
        evidence_key = "scheduled_end" if key == "end" else "scheduled_start"
        value = evidence.get(evidence_key) if isinstance(evidence, dict) else None
        return str(value) if value is not None else None

    @staticmethod
    def _add_minutes(timestamp: str, minutes: int) -> str:
        value = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M") + timedelta(minutes=minutes)
        return value.strftime("%Y-%m-%dT%H:%M")

    @staticmethod
    def _clamp(candidate: str | None, earliest: str | None, latest: str | None) -> str | None:
        if earliest is None or latest is None:
            return candidate
        if candidate is None:
            return earliest
        return min(max(candidate, earliest), latest)

    @staticmethod
    def _overtime_schedule_end(
        schedule: dict[str, object] | None, constraints: dict[str, object]
    ) -> str | None:
        if schedule is None or schedule.get("start") is None:
            return None
        headroom = constraints.get("headroom_minutes")
        if isinstance(headroom, bool) or not isinstance(headroom, (int, float)):
            return None
        planned_break_value = schedule.get("planned_break_minutes", 0)
        if isinstance(planned_break_value, bool) or not isinstance(
            planned_break_value, (int, float)
        ):
            return None
        planned_break = int(planned_break_value)
        return MockClient._add_minutes(str(schedule["start"]), int(headroom) + planned_break)
