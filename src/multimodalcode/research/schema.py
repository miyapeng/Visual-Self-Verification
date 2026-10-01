from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


ACTION_TYPES = {
    "reset",
    "navigate",
    "click",
    "fill",
    "select",
    "hover",
    "press",
    "scroll",
    "wait",
    "screenshot",
    "assert_text",
    "assert_visible",
    "assert_url",
    "assert_no_console_errors",
    "assert_style",
    "assert_canvas_drawn",
    "assert_visual_change",
    "assert_visual_signature",
}


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    description: str
    expected: str
    weight: float = 1.0
    category: str = "functional"
    source: str = "benchmark"

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ChecklistItem":
        item_id = str(value.get("id", "")).strip()
        if not item_id:
            raise ValueError("Checklist item requires a non-empty id")
        return cls(
            id=item_id,
            description=str(value.get("description", value.get("task", ""))),
            expected=str(value.get("expected", value.get("expected_result", ""))),
            weight=float(value.get("weight", value.get("max_score", 1.0))),
            category=str(value.get("category", "functional")),
            source=str(value.get("source", "benchmark")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlannedAction:
    type: str
    checklist_id: Optional[str] = None
    selector: Optional[str] = None
    role: Optional[str] = None
    name: Optional[str] = None
    text: Optional[str] = None
    value: Optional[str] = None
    url: Optional[str] = None
    key: Optional[str] = None
    expected: Optional[str] = None
    match: str = "contains"
    x: int = 0
    y: int = 0
    milliseconds: int = 0
    timeout_ms: Optional[int] = None
    minimum_ratio: float = 0.0
    reference_image: Optional[str] = None
    note: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "PlannedAction":
        action_type = str(value.get("type", "")).strip()
        if action_type not in ACTION_TYPES:
            raise ValueError(
                f"Unsupported action type {action_type!r}; expected one of "
                f"{sorted(ACTION_TYPES)}"
            )
        return cls(
            type=action_type,
            checklist_id=_optional_string(value.get("checklist_id")),
            selector=_optional_string(value.get("selector")),
            role=_optional_string(value.get("role")),
            name=_optional_string(value.get("name")),
            text=_optional_string(value.get("text")),
            value=_optional_string(value.get("value")),
            url=_optional_string(value.get("url")),
            key=_optional_string(value.get("key")),
            expected=_optional_string(value.get("expected")),
            match=str(value.get("match", "contains")),
            x=int(value.get("x", 0)),
            y=int(value.get("y", 0)),
            milliseconds=int(value.get("milliseconds", value.get("wait_ms", 0))),
            timeout_ms=(
                None if value.get("timeout_ms") is None else int(value["timeout_ms"])
            ),
            minimum_ratio=float(value.get("minimum_ratio", 0.0)),
            reference_image=_optional_string(value.get("reference_image")),
            note=_optional_string(value.get("note")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _optional_string(value: Any) -> Optional[str]:
    return None if value is None else str(value)


@dataclass(frozen=True)
class ActionPlan:
    checklist: List[ChecklistItem]
    actions: List[PlannedAction]
    planner: str = "benchmark"
    plan_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "ActionPlan":
        checklist = [ChecklistItem.from_dict(row) for row in value.get("checklist", [])]
        actions = [PlannedAction.from_dict(row) for row in value.get("actions", [])]
        if not checklist:
            raise ValueError("Action plan requires at least one checklist item")
        if not actions:
            raise ValueError("Action plan requires at least one action")
        checklist_ids = {item.id for item in checklist}
        if len(checklist_ids) != len(checklist):
            raise ValueError("Checklist ids must be unique")
        unknown = sorted(
            {
                action.checklist_id
                for action in actions
                if action.checklist_id and action.checklist_id not in checklist_ids
            }
        )
        if unknown:
            raise ValueError(f"Actions reference unknown checklist ids: {unknown}")
        return cls(
            checklist=checklist,
            actions=actions,
            planner=str(value.get("planner", "benchmark")),
            plan_version=int(value.get("plan_version", 1)),
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checklist": [item.to_dict() for item in self.checklist],
            "actions": [action.to_dict() for action in self.actions],
            "planner": self.planner,
            "plan_version": self.plan_version,
            "metadata": self.metadata,
        }
