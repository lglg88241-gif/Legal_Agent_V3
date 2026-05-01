from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DossierEntityRequestContext:
    user_id: int
    dossier_id: int
    event_type: str
    conversation_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    conversation_entity_states: list[dict[str, Any]] = field(default_factory=list)
    resolution_decisions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "dossier_id": self.dossier_id,
            "conversation_id": self.conversation_id,
            "event_type": self.event_type,
            "payload": dict(self.payload or {}),
            "conversation_entity_states": list(self.conversation_entity_states or []),
            "resolution_decisions": list(self.resolution_decisions or []),
        }


__all__ = ["DossierEntityRequestContext"]
