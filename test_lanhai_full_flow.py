"""
Test full flow: 蓝海科技 vs 蓝海科技公司 candidate generation in dossier_entity_state
"""
from app.services.dossier_entity_state import build_dossier_entity_state

# Simulate two conversation entity states with 蓝海科技 and 蓝海科技公司
conversation_entity_states = [
    {
        "conversation_id": 1,
        "entities": [
            {
                "id": "entity_1_lanhai",
                "label": "蓝海科技",
                "entity_type": "organization",
                "role": "employer",
                "status": "confirmed",
            }
        ],
        "should_build": True,
    },
    {
        "conversation_id": 2,
        "entities": [
            {
                "id": "entity_2_lanhai_company",
                "label": "蓝海科技公司",
                "entity_type": "organization",
                "role": "employer",
                "status": "confirmed",
            }
        ],
        "should_build": True,
    },
]

result = build_dossier_entity_state(
    conversation_entity_states=conversation_entity_states,
    dossier_id=999,
    user_id=1,
    resolution_decisions=[],
    conversation_summaries=[],
)

print("=== Dossier Entity State ===")
print(f"Canonical entities: {len(result.get('entities', []))}")
for entity in result.get("entities", []):
    print(f"  - {entity.get('canonical_label')} (type: {entity.get('entity_type')}, role: {entity.get('role')})")

print(f"\nConfirmation tasks: {len(result.get('confirmation_tasks', []))}")
for task in result.get("confirmation_tasks", []):
    print(f"  - Task: {task.get('source_entity', {}).get('label')} vs {task.get('target_entity', {}).get('label')}")
    print(f"    Type: {task.get('task_type')}")
    print(f"    Confidence: {task.get('confidence')}")
    print(f"    Score: {task.get('score')}")
    print(f"    Label similarity: {task.get('label_similarity')}")
    print(f"    Auto merge recommended: {task.get('auto_merge_recommended')}")
    print(f"    Requires user confirmation: {task.get('requires_user_confirmation')}")
