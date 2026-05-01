from app.services.dossier_entity_state import _score_same_entity_candidate

left = {
    "id": "entity-1",
    "canonical_label": "蓝海科技",
    "entity_type": "organization",
    "role": "employer",
    "source_labels": ["蓝海科技"],
    "mentions": [{"id": "m1", "raw_label": "蓝海科技"}],
}

right = {
    "id": "entity-2",
    "canonical_label": "蓝海科技公司",
    "entity_type": "organization",
    "role": "employer",
    "source_labels": ["蓝海科技公司"],
    "mentions": [{"id": "m2", "raw_label": "蓝海科技公司"}],
}

result = _score_same_entity_candidate(left, right)

print("蓝海科技 vs 蓝海科技公司 scoring result:")
print(f"  score: {result['score']}")
print(f"  confidence: {result['confidence']}")
print(f"  label_similarity: {result['label_similarity']}")
print(f"  role_score: {result['role_score']}")
print(f"  auto_merge_recommended: {result['auto_merge_recommended']}")
print(f"  reasons: {result['reasons']}")
