"""Small deterministic rule registry for Phase E.1.

Rules are pure callables over a mapping. Persistence and reviewer actions belong
to later Phase E work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from ai.validation.models import ValidationIssue


RuleCallable = Callable[[Mapping[str, object]], tuple[ValidationIssue, ...]]


@dataclass(frozen=True)
class RegisteredRule:
    rule_id: str
    applies_to: str
    evaluator: RuleCallable


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, RegisteredRule] = {}

    def register(self, rule: RegisteredRule) -> None:
        if not rule.rule_id.strip():
            raise ValueError("rule_id must not be empty")
        if rule.rule_id in self._rules:
            raise ValueError(f"rule already registered: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def rules_for(self, entity_type: str) -> tuple[RegisteredRule, ...]:
        return tuple(rule for rule in self._rules.values() if rule.applies_to == entity_type)

    def evaluate(self, entity_type: str, payload: Mapping[str, object]) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        for rule in self.rules_for(entity_type):
            issues.extend(rule.evaluator(payload))
        return tuple(issues)
