from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, inspect, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.database.mysql_db import Base, engine
from app.models import (
    Conversation,
    Dossier,
    DossierParticipant,
    DossierParticipantMention,
    Message,
)
from app.services.conversation_mode import (
    normalize_conversation_mode,
    normalize_persistence_consent,
)

DEFAULT_CONVERSATION_TITLE = ""
DEFAULT_FALLBACK_TITLE = "默认会话"

DEFAULT_CONVERSATION_MODE = "UNKNOWN"
DEFAULT_PERSISTENCE_CONSENT = "unknown"
DEFAULT_DOSSIER_STATUS = "active"
VALID_DOSSIER_STATUSES = {"active", "archived", "closed"}
DEFAULT_PARTICIPANT_ALIASES: dict[str, dict[str, Any]] = {}
VALID_PARTY_SIDES = {"claimant", "opponent", "neutral", "unknown"}
VALID_ENTITY_TYPES = {"person", "organization", "authority", "unknown"}
VALID_PARTICIPANT_MENTION_SOURCES = {
    "graph",
    "graph_node",
    "conversation",
    "user",
    "party_naming",
}
PARTICIPANT_RESOLVABLE_ENTITY_TYPES = {
    "person",
    "organization",
    "authority",
}
PARTICIPANT_RESOLVABLE_UNKNOWN_GRAPH_NODE_TYPES = {
    "person",
    "organization",
    "company",
    "employer",
    "authority",
    "vehicle",
    "自然人",
    "组织",
    "公司",
    "用人单位",
    "机构",
    "车辆",
}
PARTICIPANT_NON_RESOLVABLE_GRAPH_NODE_TYPES = {
    "event",
    "fact",
    "evidence",
    "document",
    "amount",
    "money",
    "time",
    "date",
    "law",
    "statute",
    "claim",
    "procedure",
    "relationship_event",
    "action",
    "issue",
    "事件",
    "事实",
    "证据",
    "文书",
    "金额",
    "金钱",
    "时间",
    "日期",
    "法条",
    "法规",
    "法律",
    "诉求",
    "程序",
    "关系事件",
    "行为",
    "争议",
    "问题",
}
RUNTIME_INDEX_SPECS = {
    "dossiers": (
        ("ix_dossiers_user_id", ("user_id",)),
        ("ix_dossiers_user_id_status", ("user_id", "status")),
        ("ix_dossiers_updated_at", ("updated_at",)),
    ),
    "conversations": (
        ("ix_conversations_user_id", ("user_id",)),
        ("ix_conversations_dossier_id", ("dossier_id",)),
        ("ix_conversations_session_id", ("session_id",)),
        ("ix_conversations_user_id_dossier_id", ("user_id", "dossier_id")),
        ("ix_conversations_user_id_updated_at", ("user_id", "updated_at")),
        ("ix_conversations_conversation_mode", ("conversation_mode",)),
    ),
    "dossier_participants": (
        ("ix_dossier_participants_dossier_id", ("dossier_id",)),
        ("ix_dossier_participants_user_id", ("user_id",)),
        ("ix_dossier_participants_dossier_id_label", ("dossier_id", "label")),
    ),
    "dossier_participant_mentions": (
        ("ix_dossier_participant_mentions_dossier_id", ("dossier_id",)),
        ("ix_dossier_participant_mentions_conversation_id", ("conversation_id",)),
        ("ix_dossier_participant_mentions_participant_id", ("participant_id",)),
        (
            "ix_dossier_participant_mentions_dossier_id_raw_label",
            ("dossier_id", "raw_label"),
        ),
        (
            "ix_dossier_participant_mentions_dossier_id_conversation_id",
            ("dossier_id", "conversation_id"),
        ),
    ),
    "messages": (
        ("ix_messages_conversation_id", ("conversation_id",)),
    ),
}


def _ensure_runtime_indexes(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    for table_name, index_specs in RUNTIME_INDEX_SPECS.items():
        if table_name not in tables:
            continue

        columns = {column["name"] for column in inspector.get_columns(table_name)}
        existing_index_names = {
            index.get("name")
            for index in inspector.get_indexes(table_name)
            if isinstance(index.get("name"), str)
        }
        for index_name, index_columns in index_specs:
            if index_name in existing_index_names:
                continue
            if any(column_name not in columns for column_name in index_columns):
                continue
            column_sql = ", ".join(index_columns)
            sync_conn.execute(text(f"CREATE INDEX {index_name} ON {table_name} ({column_sql})"))
            existing_index_names.add(index_name)


def _ensure_conversation_updated_at(sync_conn) -> None:
    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "conversations" not in tables:
        return

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "updated_at" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN updated_at DATETIME"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text(
                    "ALTER TABLE conversations "
                    "ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )
        else:
            sync_conn.execute(
                text(
                    "ALTER TABLE conversations "
                    "ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) "
            "WHERE updated_at IS NULL"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_summary" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN case_summary TEXT"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_summary TEXT DEFAULT NULL")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_summary TEXT DEFAULT NULL")
            )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET case_summary = COALESCE(case_summary, '') "
            "WHERE case_summary IS NULL"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_state_json" not in columns:
        dialect = sync_conn.dialect.name
        if dialect == "sqlite":
            sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN case_state_json TEXT"))
        elif dialect == "postgresql":
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_state_json JSONB DEFAULT NULL")
            )
        else:
            sync_conn.execute(
                text("ALTER TABLE conversations ADD COLUMN case_state_json JSON DEFAULT NULL")
            )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "case_type" not in columns:
        sync_conn.execute(
            text("ALTER TABLE conversations ADD COLUMN case_type VARCHAR(100) DEFAULT NULL")
        )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "conversation_mode" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN conversation_mode VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'"
            )
        )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET conversation_mode = 'UNKNOWN' "
            "WHERE conversation_mode IS NULL OR conversation_mode = ''"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "persistence_consent" not in columns:
        sync_conn.execute(
            text(
                "ALTER TABLE conversations "
                "ADD COLUMN persistence_consent VARCHAR(32) NOT NULL DEFAULT 'unknown'"
            )
        )

    sync_conn.execute(
        text(
            "UPDATE conversations "
            "SET persistence_consent = 'unknown' "
            "WHERE persistence_consent IS NULL OR persistence_consent = ''"
        )
    )

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "mode_confirmed_at" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN mode_confirmed_at DATETIME"))

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "mode_reason" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN mode_reason TEXT"))

    columns = {column["name"] for column in inspector.get_columns("conversations")}
    if "dossier_id" not in columns:
        sync_conn.execute(text("ALTER TABLE conversations ADD COLUMN dossier_id INTEGER"))

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "dossiers" not in tables:
        return

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN auto_summary TEXT"))

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary_updated_at" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN auto_summary_updated_at DATETIME"))

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "auto_summary_source_count" not in dossier_columns:
        if sync_conn.dialect.name == "postgresql":
            sync_conn.execute(
                text(
                    "ALTER TABLE dossiers "
                    "ADD COLUMN auto_summary_source_count INTEGER NOT NULL DEFAULT 0"
                )
            )
        else:
            sync_conn.execute(
                text(
                    "ALTER TABLE dossiers "
                    "ADD COLUMN auto_summary_source_count INTEGER NOT NULL DEFAULT 0"
                )
            )

    sync_conn.execute(
        text(
            "UPDATE dossiers "
            "SET auto_summary_source_count = COALESCE(auto_summary_source_count, 0) "
            "WHERE auto_summary_source_count IS NULL"
        )
    )

    dossier_columns = {column["name"] for column in inspector.get_columns("dossiers")}
    if "participant_aliases_json" not in dossier_columns:
        sync_conn.execute(text("ALTER TABLE dossiers ADD COLUMN participant_aliases_json TEXT"))

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "dossier_participant_mentions" in tables:
        mention_columns = {
            column["name"] for column in inspector.get_columns("dossier_participant_mentions")
        }
        if "graph_node_id" not in mention_columns:
            sync_conn.execute(
                text("ALTER TABLE dossier_participant_mentions ADD COLUMN graph_node_id VARCHAR(255)")
            )
        mention_columns = {
            column["name"] for column in inspector.get_columns("dossier_participant_mentions")
        }
        if "graph_node_label" not in mention_columns:
            sync_conn.execute(
                text(
                    "ALTER TABLE dossier_participant_mentions "
                    "ADD COLUMN graph_node_label VARCHAR(255)"
                )
            )
        mention_columns = {
            column["name"] for column in inspector.get_columns("dossier_participant_mentions")
        }
        if "graph_node_type" not in mention_columns:
            sync_conn.execute(
                text(
                    "ALTER TABLE dossier_participant_mentions "
                    "ADD COLUMN graph_node_type VARCHAR(100)"
                )
            )

    _ensure_runtime_indexes(sync_conn)


async def ensure_storage_schema(bind_engine: AsyncEngine | None = None) -> None:
    target_engine = bind_engine or engine
    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_conversation_updated_at)


def utcnow() -> datetime:
    return datetime.utcnow()


def build_conversation_title(
    content: str,
    fallback: str = "新会话",
) -> str:
    cleaned = " ".join((content or "").strip().split())
    if not cleaned:
        return fallback
    return cleaned[:24]


def normalize_dossier_status(status: str | None) -> str:
    cleaned = " ".join((status or "").strip().split()).lower()
    if cleaned not in VALID_DOSSIER_STATUSES:
        raise ValueError(f"Unsupported dossier status: {status}")
    return cleaned


def normalize_party_side(party_side: str | None) -> str:
    cleaned = " ".join((party_side or "").strip().split()).lower()
    if cleaned not in VALID_PARTY_SIDES:
        raise ValueError(f"Unsupported participant party_side: {party_side}")
    return cleaned


def normalize_entity_type(entity_type: str | None) -> str:
    cleaned = " ".join((entity_type or "").strip().split()).lower()
    if cleaned not in VALID_ENTITY_TYPES:
        raise ValueError(f"Unsupported participant entity_type: {entity_type}")
    return cleaned


def normalize_participant_mention_source(source: str | None) -> str:
    cleaned = " ".join((source or "").strip().split()).lower()
    if cleaned not in VALID_PARTICIPANT_MENTION_SOURCES:
        raise ValueError(f"Unsupported participant mention source: {source}")
    return cleaned


def _normalize_optional_text(value: str | None) -> str | None:
    cleaned = " ".join((value or "").strip().split())
    return cleaned or None


def _normalize_graph_provenance_value(value: str | None) -> str | None:
    return _normalize_optional_text(value)


def _normalize_participant_label(label: str | None) -> str:
    cleaned = " ".join((label or "").strip().split())
    if not cleaned:
        raise ValueError("Participant label is required")
    return cleaned


def _normalize_participant_role(role: str | None) -> str | None:
    return _normalize_optional_text(role)


def _normalize_participant_description(description: str | None) -> str | None:
    return _normalize_optional_text(description)


def _normalized_label_key(label: str | None) -> str:
    return " ".join((label or "").strip().split()).lower()


SELF_ANCHOR_PARTICIPANT_LABELS = {"我"}
GENERIC_PARTICIPANT_LABELS = {
    "当事人",
    "双方",
    "对方",
    "公司",
    "单位",
    "用人单位",
    "劳动者",
    "员工",
    "老板",
    "领导",
    "人事",
    "hr",
    "法院",
    "仲裁委",
}
GENERIC_PARTICIPANT_HINTS: dict[str, dict[str, str]] = {
    "当事人": {"role": "当事人", "party_side": "claimant", "entity_type": "person"},
    "劳动者": {"role": "劳动者", "party_side": "claimant", "entity_type": "person"},
    "员工": {"role": "员工", "party_side": "claimant", "entity_type": "person"},
    "对方": {"role": "对方当事人", "party_side": "opponent", "entity_type": "unknown"},
    "公司": {"role": "用人单位", "party_side": "opponent", "entity_type": "organization"},
    "单位": {"role": "用人单位", "party_side": "opponent", "entity_type": "organization"},
    "用人单位": {"role": "用人单位", "party_side": "opponent", "entity_type": "organization"},
    "老板": {"role": "老板", "party_side": "opponent", "entity_type": "person"},
    "领导": {"role": "领导", "party_side": "opponent", "entity_type": "person"},
    "人事": {"role": "人事", "party_side": "opponent", "entity_type": "person"},
    "hr": {"role": "HR", "party_side": "opponent", "entity_type": "person"},
    "法院": {"role": "法院", "party_side": "neutral", "entity_type": "authority"},
    "仲裁委": {"role": "仲裁委", "party_side": "neutral", "entity_type": "authority"},
}
ORGANIZATION_LABEL_TOKENS = ("公司", "单位", "集团", "医院", "学校", "银行", "厂")
PERSON_ROLE_TOKENS: tuple[tuple[str, str], ...] = (
    ("经理", "经理"),
    ("总监", "总监"),
    ("主任", "主任"),
    ("律师", "律师"),
)


def is_self_anchor_participant_label(label: str | None) -> bool:
    return _normalized_label_key(label) in SELF_ANCHOR_PARTICIPANT_LABELS


def is_generic_participant_label(label: str | None) -> bool:
    return _normalized_label_key(label) in GENERIC_PARTICIPANT_LABELS


def is_specific_participant_label(label: str | None) -> bool:
    normalized = _normalized_label_key(label)
    return bool(normalized) and not is_generic_participant_label(normalized)


async def create_conversation_record(
    db: AsyncSession,
    user_id: int,
    title: str = DEFAULT_CONVERSATION_TITLE,
    dossier_id: int | None = None,
) -> Conversation:
    now = utcnow()
    conversation = Conversation(
        user_id=user_id,
        dossier_id=dossier_id,
        session_id=str(uuid4()),
        title=(title or "").strip(),
        conversation_mode=DEFAULT_CONVERSATION_MODE,
        persistence_consent=DEFAULT_PERSISTENCE_CONSENT,
        updated_at=now,
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def create_dossier_record(
    db: AsyncSession,
    user_id: int,
    *,
    title: str,
    case_type: str | None = None,
    summary: str | None = None,
    status: str = DEFAULT_DOSSIER_STATUS,
) -> Dossier:
    dossier = Dossier(
        user_id=user_id,
        title=(title or "").strip(),
        case_type=(case_type or "").strip() or None,
        summary=(summary or "").strip() or None,
        status=normalize_dossier_status(status),
        updated_at=utcnow(),
    )
    db.add(dossier)
    await db.flush()
    return dossier


async def get_conversation_for_user(
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> Conversation | None:
    return await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )


async def get_dossier_for_user(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> Dossier | None:
    return await db.scalar(
        select(Dossier).where(
            Dossier.id == dossier_id,
            Dossier.user_id == user_id,
        )
    )


async def list_user_conversations(
    db: AsyncSession,
    user_id: int,
) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(result)


async def list_user_dossiers(
    db: AsyncSession,
    user_id: int,
) -> list[Dossier]:
    result = await db.scalars(
        select(Dossier)
        .where(Dossier.user_id == user_id)
        .order_by(Dossier.updated_at.desc(), Dossier.id.desc())
    )
    return list(result)


async def list_dossier_conversations(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[Conversation]:
    result = await db.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.dossier_id == dossier_id,
        )
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    )
    return list(result)


async def list_user_conversations_by_ids(
    db: AsyncSession,
    user_id: int,
    conversation_ids: list[int],
) -> list[Conversation]:
    normalized_ids = [conversation_id for conversation_id in conversation_ids if conversation_id > 0]
    if not normalized_ids:
        return []

    result = await db.scalars(
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.id.in_(normalized_ids),
        )
        .order_by(Conversation.id.asc())
    )
    return list(result)


async def list_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
    )
    return list(result)


async def list_recent_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 6,
) -> list[Message]:
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    return list(reversed(list(result)))


async def get_message_for_conversation(
    db: AsyncSession,
    conversation_id: int,
    message_id: int,
) -> Message | None:
    return await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.conversation_id == conversation_id,
        )
    )


async def touch_conversation(
    db: AsyncSession,
    conversation: Conversation | None = None,
    conversation_id: int | None = None,
) -> Conversation | None:
    target = conversation
    if target is None and conversation_id is not None:
        target = await db.get(Conversation, conversation_id)

    if target is None:
        return None

    target.updated_at = utcnow()
    db.add(target)
    await db.flush()
    return target


async def touch_dossier(
    db: AsyncSession,
    dossier: Dossier | None = None,
    dossier_id: int | None = None,
) -> Dossier | None:
    target = dossier
    if target is None and dossier_id is not None:
        target = await db.get(Dossier, dossier_id)

    if target is None:
        return None

    target.updated_at = utcnow()
    db.add(target)
    await db.flush()
    return target


async def update_conversation_title(
    db: AsyncSession,
    conversation: Conversation,
    title: str,
) -> Conversation:
    conversation.title = (title or "").strip()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_dossier_fields(
    db: AsyncSession,
    dossier: Dossier,
    *,
    title: str | None = None,
    case_type: str | None = None,
    summary: str | None = None,
    status: str | None = None,
) -> Dossier:
    if title is not None:
        dossier.title = (title or "").strip()
    if case_type is not None:
        dossier.case_type = (case_type or "").strip() or None
    if summary is not None:
        dossier.summary = (summary or "").strip() or None
    if status is not None:
        dossier.status = normalize_dossier_status(status)
    await touch_dossier(db, dossier=dossier)
    return dossier


async def archive_dossier_record(
    db: AsyncSession,
    dossier: Dossier,
) -> Dossier:
    dossier.status = "archived"
    await touch_dossier(db, dossier=dossier)
    return dossier


async def hard_delete_dossier_record(
    db: AsyncSession,
    dossier: Dossier,
) -> dict[str, int]:
    dossier_id = int(dossier.id)

    conversation_ids = list(
        (
            await db.execute(
                select(Conversation.id).where(Conversation.dossier_id == dossier_id)
            )
        ).scalars()
    )
    detached_conversations = len(conversation_ids)

    mention_ids = list(
        (
            await db.execute(
                select(DossierParticipantMention.id).where(
                    DossierParticipantMention.dossier_id == dossier_id
                )
            )
        ).scalars()
    )
    deleted_mentions = len(mention_ids)

    participant_ids = list(
        (
            await db.execute(
                select(DossierParticipant.id).where(
                    DossierParticipant.dossier_id == dossier_id
                )
            )
        ).scalars()
    )
    deleted_participants = len(participant_ids)

    if detached_conversations:
        await db.execute(
            update(Conversation)
            .where(Conversation.dossier_id == dossier_id)
            .values(dossier_id=None)
        )

    if deleted_mentions:
        await db.execute(
            delete(DossierParticipantMention).where(
                DossierParticipantMention.dossier_id == dossier_id
            )
        )

    if deleted_participants:
        await db.execute(
            delete(DossierParticipant).where(DossierParticipant.dossier_id == dossier_id)
        )

    await db.execute(delete(Dossier).where(Dossier.id == dossier_id))

    return {
        "dossier_id": dossier_id,
        "detached_conversations": detached_conversations,
        "deleted_participants": deleted_participants,
        "deleted_mentions": deleted_mentions,
    }


async def update_dossier_auto_summary(
    db: AsyncSession,
    dossier: Dossier,
    *,
    auto_summary: str,
    auto_summary_source_count: int,
) -> Dossier:
    dossier.auto_summary = (auto_summary or "").strip() or None
    dossier.auto_summary_updated_at = utcnow() if dossier.auto_summary else None
    dossier.auto_summary_source_count = max(int(auto_summary_source_count or 0), 0)
    await touch_dossier(db, dossier=dossier)
    return dossier


def _load_participant_aliases(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return dict(DEFAULT_PARTICIPANT_ALIASES)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return dict(DEFAULT_PARTICIPANT_ALIASES)
    return parsed if isinstance(parsed, dict) else dict(DEFAULT_PARTICIPANT_ALIASES)


def _normalize_participant_aliases(
    participant_aliases: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(participant_aliases, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for key, value in participant_aliases.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        label = str(value.get("label") or "").strip()
        role = str(value.get("role") or "").strip()
        party_side = str(value.get("party_side") or "").strip()
        if not label and not role:
            continue
        normalized[key] = {
            "label": label,
            "role": role,
            "party_side": party_side or key,
            "confirmed_by_user": True,
        }
    return normalized


async def update_dossier_participant_aliases(
    db: AsyncSession,
    dossier: Dossier,
    participant_aliases: dict[str, Any] | None,
) -> Dossier:
    normalized = _normalize_participant_aliases(participant_aliases)
    dossier.participant_aliases_json = json.dumps(normalized, ensure_ascii=False) if normalized else None
    await _sync_aliases_to_confirmed_participants(db, dossier, normalized)
    await touch_dossier(db, dossier=dossier)
    return dossier


def serialize_dossier_participant(participant: DossierParticipant) -> dict[str, Any]:
    return {
        "id": participant.id,
        "dossier_id": participant.dossier_id,
        "user_id": participant.user_id,
        "label": participant.label,
        "role": participant.role or "",
        "party_side": normalize_party_side(getattr(participant, "party_side", "unknown")),
        "entity_type": normalize_entity_type(getattr(participant, "entity_type", "unknown")),
        "description": participant.description or "",
        "confirmed_by_user": bool(getattr(participant, "confirmed_by_user", False)),
        "created_at": participant.created_at.isoformat() if participant.created_at else None,
        "updated_at": participant.updated_at.isoformat() if participant.updated_at else None,
    }


def serialize_dossier_participant_mention(
    mention: DossierParticipantMention,
    participant_lookup: dict[int, DossierParticipant] | None = None,
) -> dict[str, Any]:
    participant = None
    if participant_lookup is not None:
        participant = participant_lookup.get(getattr(mention, "participant_id", 0))

    payload = {
        "id": mention.id,
        "dossier_id": mention.dossier_id,
        "conversation_id": mention.conversation_id,
        "participant_id": mention.participant_id,
        "raw_label": mention.raw_label,
        "graph_node_id": getattr(mention, "graph_node_id", None),
        "graph_node_label": getattr(mention, "graph_node_label", None),
        "graph_node_type": getattr(mention, "graph_node_type", None),
        "source": normalize_participant_mention_source(getattr(mention, "source", "graph")),
        "confidence": mention.confidence,
        "confirmed_by_user": bool(getattr(mention, "confirmed_by_user", False)),
        "created_at": mention.created_at.isoformat() if mention.created_at else None,
    }
    if participant is not None:
        payload["participant_label"] = participant.label
        payload["participant_role"] = participant.role or ""
        payload["participant_party_side"] = normalize_party_side(
            getattr(participant, "party_side", "unknown")
        )
        payload["participant_entity_type"] = normalize_entity_type(
            getattr(participant, "entity_type", "unknown")
        )
    return payload


async def list_dossier_participants(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[DossierParticipant]:
    result = await db.scalars(
        select(DossierParticipant)
        .where(
            DossierParticipant.user_id == user_id,
            DossierParticipant.dossier_id == dossier_id,
        )
        .order_by(DossierParticipant.id.asc())
    )
    return list(result)


async def get_dossier_participant_for_user(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
    participant_id: int,
) -> DossierParticipant | None:
    return await db.scalar(
        select(DossierParticipant).where(
            DossierParticipant.id == participant_id,
            DossierParticipant.user_id == user_id,
            DossierParticipant.dossier_id == dossier_id,
        )
    )


async def list_dossier_participant_mentions(
    db: AsyncSession,
    user_id: int,
    dossier_id: int,
) -> list[DossierParticipantMention]:
    result = await db.scalars(
        select(DossierParticipantMention)
        .join(
            DossierParticipant,
            DossierParticipant.id == DossierParticipantMention.participant_id,
        )
        .where(
            DossierParticipant.user_id == user_id,
            DossierParticipantMention.dossier_id == dossier_id,
        )
        .order_by(
            DossierParticipantMention.conversation_id.asc(),
            DossierParticipantMention.id.asc(),
        )
    )
    return list(result)


async def create_dossier_participant_record(
    db: AsyncSession,
    *,
    dossier: Dossier,
    user_id: int,
    label: str,
    role: str | None = None,
    party_side: str = "unknown",
    entity_type: str = "unknown",
    description: str | None = None,
    confirmed_by_user: bool = True,
) -> DossierParticipant:
    participant = DossierParticipant(
        dossier_id=dossier.id,
        user_id=user_id,
        label=_normalize_participant_label(label),
        role=_normalize_participant_role(role),
        party_side=normalize_party_side(party_side),
        entity_type=normalize_entity_type(entity_type),
        description=_normalize_participant_description(description),
        confirmed_by_user=bool(confirmed_by_user),
        updated_at=utcnow(),
    )
    db.add(participant)
    await db.flush()
    await touch_dossier(db, dossier=dossier)
    return participant


async def update_dossier_participant_record(
    db: AsyncSession,
    participant: DossierParticipant,
    *,
    label: str | None = None,
    role: str | None = None,
    party_side: str | None = None,
    entity_type: str | None = None,
    description: str | None = None,
) -> DossierParticipant:
    if label is not None:
        participant.label = _normalize_participant_label(label)
    if role is not None:
        participant.role = _normalize_participant_role(role)
    if party_side is not None:
        participant.party_side = normalize_party_side(party_side)
    if entity_type is not None:
        participant.entity_type = normalize_entity_type(entity_type)
    if description is not None:
        participant.description = _normalize_participant_description(description)
    participant.updated_at = utcnow()
    db.add(participant)
    await db.flush()
    return participant


def build_dossier_participant_lookup(
    participants: list[DossierParticipant],
) -> dict[int, DossierParticipant]:
    return {
        participant.id: participant
        for participant in participants
        if isinstance(getattr(participant, "id", None), int)
    }


def _mention_key(
    *,
    conversation_id: int,
    raw_label: str,
    source: str,
) -> tuple[int, str, str]:
    return (
        int(conversation_id),
        " ".join((raw_label or "").strip().split()).lower(),
        normalize_participant_mention_source(source),
    )


async def resolve_dossier_participant_mentions(
    db: AsyncSession,
    *,
    dossier: Dossier,
    user_id: int,
    participant: DossierParticipant,
    mentions: list[dict[str, Any]],
) -> list[DossierParticipantMention]:
    normalized_mentions: list[dict[str, Any]] = []
    required_conversation_ids: list[int] = []
    seen_keys: set[tuple[int, str, str]] = set()

    for item in mentions:
        if not isinstance(item, dict):
            continue
        conversation_id = int(item.get("conversation_id") or 0)
        raw_label = _normalize_participant_label(item.get("raw_label"))
        source = normalize_participant_mention_source(item.get("source") or "graph")
        key = _mention_key(
            conversation_id=conversation_id,
            raw_label=raw_label,
            source=source,
        )
        if conversation_id <= 0 or key in seen_keys:
            continue
        seen_keys.add(key)
        required_conversation_ids.append(conversation_id)
        confidence_value = item.get("confidence")
        try:
            confidence = float(confidence_value) if confidence_value is not None else None
        except (TypeError, ValueError):
            confidence = None
        normalized_mentions.append(
            {
                "conversation_id": conversation_id,
                "raw_label": raw_label,
                "graph_node_id": _normalize_graph_provenance_value(item.get("graph_node_id")),
                "graph_node_label": _normalize_graph_provenance_value(
                    item.get("graph_node_label")
                ),
                "graph_node_type": _normalize_graph_provenance_value(item.get("graph_node_type")),
                "source": source,
                "confidence": confidence,
            }
        )

    conversations = await list_user_conversations_by_ids(db, user_id, required_conversation_ids)
    conversation_lookup = {conversation.id: conversation for conversation in conversations}
    for mention in normalized_mentions:
        conversation = conversation_lookup.get(mention["conversation_id"])
        if conversation is None or getattr(conversation, "dossier_id", None) != dossier.id:
            raise ValueError("Mention conversation must belong to the current user's dossier")

    existing_mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    existing_lookup = {
        _mention_key(
            conversation_id=mention.conversation_id,
            raw_label=mention.raw_label,
            source=mention.source,
        ): mention
        for mention in existing_mentions
    }

    resolved_mentions: list[DossierParticipantMention] = []
    for mention_payload in normalized_mentions:
        key = _mention_key(
            conversation_id=mention_payload["conversation_id"],
            raw_label=mention_payload["raw_label"],
            source=mention_payload["source"],
        )
        existing = existing_lookup.get(key)
        if existing is None:
            existing = DossierParticipantMention(
                dossier_id=dossier.id,
                conversation_id=mention_payload["conversation_id"],
                participant_id=participant.id,
                raw_label=mention_payload["raw_label"],
                graph_node_id=mention_payload["graph_node_id"],
                graph_node_label=mention_payload["graph_node_label"],
                graph_node_type=mention_payload["graph_node_type"],
                source=mention_payload["source"],
                confidence=mention_payload["confidence"],
                confirmed_by_user=True,
            )
            db.add(existing)
        else:
            existing.participant_id = participant.id
            if mention_payload["graph_node_id"] is not None:
                existing.graph_node_id = mention_payload["graph_node_id"]
            if mention_payload["graph_node_label"] is not None:
                existing.graph_node_label = mention_payload["graph_node_label"]
            if mention_payload["graph_node_type"] is not None:
                existing.graph_node_type = mention_payload["graph_node_type"]
            existing.confidence = mention_payload["confidence"]
            existing.confirmed_by_user = True
            db.add(existing)
        resolved_mentions.append(existing)

    if resolved_mentions:
        await db.flush()
        await touch_dossier(db, dossier=dossier)
    return resolved_mentions


def _seed_alias_participant_defaults(alias_key: str) -> tuple[str, str]:
    if alias_key == "primary_party":
        return ("claimant", "person")
    if alias_key == "opposing_party":
        return ("opponent", "organization")
    return ("unknown", "unknown")


async def _sync_aliases_to_confirmed_participants(
    db: AsyncSession,
    dossier: Dossier,
    participant_aliases: dict[str, dict[str, Any]],
) -> None:
    if not participant_aliases:
        return

    participants = await list_dossier_participants(db, dossier.user_id, dossier.id)
    for alias_key, alias_value in participant_aliases.items():
        if not isinstance(alias_value, dict):
            continue
        label = str(alias_value.get("label") or "").strip()
        role = str(alias_value.get("role") or "").strip() or None
        if not label:
            continue
        if is_generic_participant_label(label) and not is_self_anchor_participant_label(label):
            continue

        default_party_side, default_entity_type = _seed_alias_participant_defaults(alias_key)
        target = next(
            (
                participant
                for participant in participants
                if participant.party_side == default_party_side and participant.label == label
            ),
            None,
        )
        if target is None:
            same_side_matches = [
                participant
                for participant in participants
                if participant.party_side == default_party_side
            ]
            if len(same_side_matches) == 1:
                target = same_side_matches[0]

        if target is None:
            target = await create_dossier_participant_record(
                db,
                dossier=dossier,
                user_id=dossier.user_id,
                label=label,
                role=role,
                party_side=default_party_side,
                entity_type=default_entity_type,
                description=None,
                confirmed_by_user=True,
            )
            participants.append(target)
            continue

        await update_dossier_participant_record(
            db,
            target,
            label=label,
            role=role,
            party_side=default_party_side,
            entity_type=default_entity_type,
        )


async def update_conversation_case_summary(
    db: AsyncSession,
    conversation: Conversation,
    case_summary: str,
) -> Conversation:
    conversation.case_summary = (case_summary or "").strip()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_case_state(
    db: AsyncSession,
    conversation: Conversation,
    case_state_dict: dict[str, Any],
) -> Conversation:
    """Surgery #13: 持久化 CaseState 的完整 JSON 快照。"""
    conversation.case_state_json = case_state_dict
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_case_type(
    db: AsyncSession,
    conversation: Conversation,
    case_type: str,
) -> Conversation:
    """Surgery #13: 更新案件类型标签。"""
    conversation.case_type = (case_type or "").strip() or None
    await touch_conversation(db, conversation=conversation)
    return conversation


async def update_conversation_mode_state(
    db: AsyncSession,
    conversation: Conversation,
    *,
    conversation_mode: str | None = None,
    persistence_consent: str | None = None,
    mode_reason: str | None = None,
    mark_confirmed: bool = False,
) -> Conversation:
    if conversation_mode is not None:
        conversation.conversation_mode = normalize_conversation_mode(conversation_mode)
    if persistence_consent is not None:
        conversation.persistence_consent = normalize_persistence_consent(persistence_consent)
    if mode_reason is not None:
        conversation.mode_reason = (mode_reason or "").strip() or None
    if mark_confirmed:
        conversation.mode_confirmed_at = utcnow()
    await touch_conversation(db, conversation=conversation)
    return conversation


async def attach_conversation_to_dossier(
    db: AsyncSession,
    conversation: Conversation,
    dossier_id: int | None,
) -> Conversation:
    conversation.dossier_id = dossier_id
    await touch_conversation(db, conversation=conversation)
    return conversation


async def store_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        message_metadata=metadata or {},
    )
    db.add(message)
    await db.flush()
    await touch_conversation(db, conversation_id=conversation_id)
    return message


async def _delete_conversation_messages(db: AsyncSession, conversation_id: int) -> None:
    await db.execute(delete(Message).where(Message.conversation_id == conversation_id))


async def _delete_conversation_participant_mentions(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await db.execute(
        delete(DossierParticipantMention).where(
            DossierParticipantMention.conversation_id == conversation_id
        )
    )


async def delete_conversation_record(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await _delete_conversation_participant_mentions(db, conversation_id)
    await _delete_conversation_messages(db, conversation_id)
    await db.execute(delete(Conversation).where(Conversation.id == conversation_id))


async def clear_conversation_messages(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    await _delete_conversation_participant_mentions(db, conversation_id)
    await _delete_conversation_messages(db, conversation_id)
    conversation = await db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.case_summary = ""
        conversation.case_state_json = None
        conversation.case_type = None
        conversation.conversation_mode = DEFAULT_CONVERSATION_MODE
        conversation.persistence_consent = DEFAULT_PERSISTENCE_CONSENT
        conversation.mode_confirmed_at = None
        conversation.mode_reason = None
        await touch_conversation(db, conversation=conversation)


async def maybe_update_conversation_title(
    db: AsyncSession,
    conversation: Conversation,
    user_content: str,
) -> None:
    if conversation.title and conversation.title not in {
        DEFAULT_CONVERSATION_TITLE,
        DEFAULT_FALLBACK_TITLE,
        "新会话",
    }:
        return

    conversation.title = build_conversation_title(user_content)
    db.add(conversation)
    await db.flush()


async def load_conversation_case_summary(
    db: AsyncSession,
    conversation_id: int,
) -> str:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return ""
    return (conversation.case_summary or "").strip()


async def load_conversation_case_state(
    db: AsyncSession,
    conversation_id: int,
) -> dict[str, Any] | None:
    """Surgery #13: 加载上次保存的 CaseState JSON 快照。"""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return None
    return conversation.case_state_json


async def load_conversation_persistence_consent(
    db: AsyncSession,
    conversation_id: int,
) -> str:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        return DEFAULT_PERSISTENCE_CONSENT
    return normalize_persistence_consent(
        getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
    )


async def search_user_cases(
    db: AsyncSession,
    user_id: int,
    case_type: str | None = None,
    keyword: str | None = None,
    limit: int = 10,
) -> list[Conversation]:
    """Surgery #13: 按类型或关键词检索用户的历史案件。"""
    query = select(Conversation).where(Conversation.user_id == user_id)
    if case_type:
        query = query.where(Conversation.case_type == case_type)
    if keyword:
        query = query.where(
            or_(
                Conversation.case_summary.contains(keyword),
                Conversation.title.contains(keyword),
            )
        )
    query = query.order_by(Conversation.updated_at.desc()).limit(limit)
    result = await db.scalars(query)
    return list(result)

def _normalized_label_key(label: str | None) -> str:
    return " ".join((label or "").strip().split()).lower()


def _display_label(value: str | None, fallback: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    return cleaned or fallback


def _infer_candidate_shape(
    raw_label: str,
    *,
    participant_aliases: dict[str, Any] | None = None,
) -> dict[str, str]:
    normalized_label = _normalized_label_key(raw_label)
    normalized_aliases = _normalize_participant_aliases(
        participant_aliases if isinstance(participant_aliases, dict) else None
    )
    primary_role = str(normalized_aliases.get("primary_party", {}).get("role") or "").strip()
    opposing_role = str(normalized_aliases.get("opposing_party", {}).get("role") or "").strip()

    if is_self_anchor_participant_label(raw_label):
        return {
            "label_kind": "specific",
            "suggested_label": raw_label,
            "role": primary_role or "本人",
            "party_side": "claimant",
            "entity_type": "person",
        }

    generic_hint = GENERIC_PARTICIPANT_HINTS.get(normalized_label)
    if generic_hint is not None:
        role = str(generic_hint.get("role") or "").strip()
        if normalized_label in {"当事人", "劳动者", "员工"} and primary_role:
            role = primary_role
        elif normalized_label in {"公司", "单位", "用人单位"} and opposing_role:
            role = opposing_role
        return {
            "label_kind": "generic",
            "suggested_label": raw_label,
            "role": role,
            "party_side": str(generic_hint.get("party_side") or "unknown"),
            "entity_type": str(generic_hint.get("entity_type") or "unknown"),
        }

    if any(token in raw_label for token in ORGANIZATION_LABEL_TOKENS):
        return {
            "label_kind": "specific",
            "suggested_label": raw_label,
            "role": opposing_role or "",
            "party_side": "opponent",
            "entity_type": "organization",
        }

    for token, role in PERSON_ROLE_TOKENS:
        if token in raw_label:
            return {
                "label_kind": "specific",
                "suggested_label": raw_label,
                "role": role,
                "party_side": "opponent",
                "entity_type": "person",
            }

    if "法院" in raw_label:
        return {
            "label_kind": "specific",
            "suggested_label": raw_label,
            "role": "法院",
            "party_side": "neutral",
            "entity_type": "authority",
        }
    if "仲裁" in raw_label:
        return {
            "label_kind": "specific",
            "suggested_label": raw_label,
            "role": "仲裁委",
            "party_side": "neutral",
            "entity_type": "authority",
        }

    return {
        "label_kind": "specific",
        "suggested_label": raw_label,
        "role": "",
        "party_side": "unknown",
        "entity_type": "unknown",
    }


def _find_exact_label_matches(
    participants: list[DossierParticipant],
    raw_label: str,
) -> list[DossierParticipant]:
    normalized_label = _normalized_label_key(raw_label)
    return [
        participant
        for participant in participants
        if _normalized_label_key(participant.label) == normalized_label
    ]


def _select_exact_candidate_participant(
    participants: list[DossierParticipant],
    *,
    raw_label: str,
    participant_aliases: dict[str, Any] | None = None,
) -> tuple[DossierParticipant | None, str | None]:
    exact_label_matches = _find_exact_label_matches(participants, raw_label)
    if len(exact_label_matches) == 1:
        return exact_label_matches[0], "exact_label"
    if exact_label_matches:
        return None, None

    normalized_aliases = _normalize_participant_aliases(
        participant_aliases if isinstance(participant_aliases, dict) else None
    )
    normalized_label = _normalized_label_key(raw_label)
    for alias_key, alias_value in normalized_aliases.items():
        alias_label = str(alias_value.get("label") or "").strip()
        if not alias_label or _normalized_label_key(alias_label) != normalized_label:
            continue
        if is_generic_participant_label(alias_label) and not is_self_anchor_participant_label(alias_label):
            return None, None
        default_party_side, _default_entity_type = _seed_alias_participant_defaults(alias_key)
        same_side_matches = [
            participant
            for participant in participants
            if participant.party_side == default_party_side
        ]
        if len(same_side_matches) == 1:
            return same_side_matches[0], "exact_alias"
    return None, None


def _build_match_candidate_entry(
    participant: DossierParticipant,
    *,
    match_type: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "participant_id": participant.id,
        "display_label": participant.label,
        "participant_label": participant.label,
        "participant_role": participant.role or "",
        "participant_party_side": normalize_party_side(getattr(participant, "party_side", "unknown")),
        "participant_entity_type": normalize_entity_type(
            getattr(participant, "entity_type", "unknown")
        ),
        "match_type": match_type,
        "reason": reason,
    }


def _dedupe_match_candidates(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in items:
        participant_id = int(item.get("participant_id") or 0)
        if participant_id <= 0 or participant_id in seen_ids:
            continue
        seen_ids.add(participant_id)
        deduped.append(item)
    return deduped


def _build_generic_match_candidates(
    participants: list[DossierParticipant],
    *,
    raw_label: str,
    party_side: str,
    entity_type: str,
) -> list[dict[str, Any]]:
    normalized_label = _normalized_label_key(raw_label)
    if normalized_label == "双方":
        matched_participants = list(participants)
    elif normalized_label in {"当事人", "劳动者", "员工"}:
        matched_participants = [
            participant
            for participant in participants
            if participant.party_side == "claimant"
        ]
    elif normalized_label == "对方":
        matched_participants = [
            participant
            for participant in participants
            if participant.party_side == "opponent"
        ]
    elif normalized_label in {"公司", "单位", "用人单位"}:
        matched_participants = [
            participant
            for participant in participants
            if participant.party_side == "opponent"
            and participant.entity_type == "organization"
        ]
    elif normalized_label in {"老板", "领导", "人事", "hr"}:
        matched_participants = [
            participant
            for participant in participants
            if participant.party_side == "opponent"
            and participant.entity_type == "person"
        ]
    elif normalized_label in {"法院", "仲裁委"}:
        matched_participants = [
            participant
            for participant in participants
            if participant.entity_type == "authority"
        ]
    else:
        matched_participants = [
            participant
            for participant in participants
            if (party_side == "unknown" or participant.party_side == party_side)
            and (entity_type == "unknown" or participant.entity_type == entity_type)
        ]

    return _dedupe_match_candidates(
        [
            _build_match_candidate_entry(
                participant,
                match_type="generic_hint",
                reason="这个称呼是泛称，需要用户确认它是否指向该已确认主体。",
            )
            for participant in matched_participants
        ]
    )


def _build_candidate_reason(
    *,
    raw_label: str,
    label_kind: str,
    exact_match_type: str | None,
    has_match_candidates: bool,
) -> str:
    if exact_match_type == "exact_label":
        return f"“{raw_label}”与已有主体显示名精确匹配。"
    if exact_match_type == "exact_alias":
        return f"“{raw_label}”与案卷内已确认别名精确匹配。"
    if label_kind == "generic":
        if has_match_candidates:
            return f"“{raw_label}”是泛称，系统不能直接确认，需要用户判断它指向哪个已有主体。"
        return f"“{raw_label}”是泛称，系统不能直接创建为已确认主体。"
    return f"“{raw_label}”是新的具体称呼，需要确认它是否为独立主体或已有主体别名。"


def _build_candidate_payload(
    *,
    candidate_key: str,
    raw_label: str,
    mentions: list[dict[str, Any]],
    participants: list[DossierParticipant],
    participant_aliases: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not mentions:
        return None

    candidate_shape = _infer_candidate_shape(
        raw_label,
        participant_aliases=participant_aliases,
    )
    exact_label_matches = _find_exact_label_matches(participants, raw_label)
    existing_participant, exact_match_type = _select_exact_candidate_participant(
        participants,
        raw_label=raw_label,
        participant_aliases=participant_aliases,
    )
    if existing_participant is not None:
        match_candidates = [
            _build_match_candidate_entry(
                existing_participant,
                match_type=str(exact_match_type or "exact_label"),
                reason=(
                    "该称呼与已确认主体显示名精确匹配。"
                    if exact_match_type != "exact_alias"
                    else "该称呼与案卷内已确认别名精确匹配。"
                ),
            )
        ]
    elif exact_label_matches:
        match_candidates = [
            _build_match_candidate_entry(
                participant,
                match_type="duplicate_exact_label",
                reason="存在多个同名已确认主体，需要用户进一步确认。",
            )
            for participant in exact_label_matches
        ]
    elif candidate_shape["label_kind"] == "generic":
        match_candidates = _build_generic_match_candidates(
            participants,
            raw_label=raw_label,
            party_side=candidate_shape["party_side"],
            entity_type=candidate_shape["entity_type"],
        )
    else:
        match_candidates = []

    raw_labels = list(dict.fromkeys(item["raw_label"] for item in mentions if item.get("raw_label")))
    serialized_mentions = [_serialize_resolution_node_record(item) for item in mentions]
    return {
        "candidate_key": candidate_key,
        "task_key": candidate_key,
        "task_type": "raw_label_resolution",
        "raw_label": raw_label,
        "raw_labels": raw_labels,
        "suggested_label": candidate_shape["suggested_label"],
        "suggested_role": candidate_shape["role"],
        "label_kind": candidate_shape["label_kind"],
        "party_side": candidate_shape["party_side"],
        "entity_type": candidate_shape["entity_type"],
        "reason": _build_candidate_reason(
            raw_label=raw_label,
            label_kind=candidate_shape["label_kind"],
            exact_match_type=exact_match_type,
            has_match_candidates=bool(match_candidates),
        ),
        "mentions": serialized_mentions,
        "match_candidates": match_candidates,
        "existing_participant_id": existing_participant.id if existing_participant is not None else None,
        "existing_participant_label": existing_participant.label if existing_participant is not None else "",
        "recommended_participant_id": existing_participant.id if existing_participant is not None else None,
        "recommended_participant_label": existing_participant.label if existing_participant is not None else "",
        "confidence": None,
        "can_create_new": candidate_shape["label_kind"] == "specific",
        "can_link_existing": bool(participants),
        "can_mark_non_entity": candidate_shape["label_kind"] == "generic",
        "can_mark_not_same": False,
        "can_skip": True,
    }


def _resolution_node_legacy_label_key(node_record: dict[str, Any]) -> tuple[int, str] | None:
    conversation_id = int(node_record.get("conversation_id") or 0)
    normalized_label = str(
        node_record.get("normalized_label") or _normalized_label_key(node_record.get("raw_label"))
    ).strip()
    if conversation_id <= 0 or not normalized_label:
        return None
    return conversation_id, normalized_label


def _build_resolution_node_record(
    node_payload: dict[str, Any],
    *,
    participant_aliases: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(node_payload, dict):
        return None

    raw_label = _display_label(node_payload.get("raw_label") or node_payload.get("label"), "")
    if not raw_label:
        return None

    conversation_id = node_payload.get("conversation_id")
    if conversation_id is None and isinstance(node_payload.get("properties"), dict):
        conversation_id = node_payload["properties"].get("conversation_id")
    try:
        normalized_conversation_id = int(conversation_id or 0)
    except (TypeError, ValueError):
        normalized_conversation_id = 0
    if normalized_conversation_id <= 0:
        return None

    graph_node_label = (
        _display_label(node_payload.get("graph_node_label") or node_payload.get("label"), "")
        or raw_label
    )
    graph_node_type = str(
        node_payload.get("graph_node_type")
        or node_payload.get("node_type")
        or node_payload.get("type")
        or ""
    ).strip() or None
    inferred_shape = _infer_candidate_shape(raw_label, participant_aliases=participant_aliases)
    inferred_entity_type = str(inferred_shape.get("entity_type") or "unknown").strip() or "unknown"
    normalized_entity_type = (
        graph_node_type
        if graph_node_type in VALID_ENTITY_TYPES
        else normalize_entity_type(inferred_entity_type)
    )
    label_kind = "generic" if is_generic_participant_label(raw_label) else "specific"
    graph_node_id = str(node_payload.get("graph_node_id") or node_payload.get("id") or "").strip() or None
    normalized_label = _normalized_label_key(raw_label)

    return {
        "conversation_id": normalized_conversation_id,
        "graph_node_id": graph_node_id,
        "raw_label": raw_label,
        "normalized_label": normalized_label,
        "graph_node_label": graph_node_label,
        "graph_node_type": graph_node_type,
        "entity_type": normalized_entity_type,
        "label_kind": label_kind,
        "source": str(node_payload.get("source") or "graph").strip() or "graph",
        "confidence": node_payload.get("confidence"),
    }


def _serialize_resolution_node_record(node_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "conversation_id": int(node_record.get("conversation_id") or 0),
        "raw_label": str(node_record.get("raw_label") or "").strip(),
        "normalized_label": str(
            node_record.get("normalized_label") or _normalized_label_key(node_record.get("raw_label"))
        ).strip(),
        "graph_node_id": str(node_record.get("graph_node_id") or "").strip() or None,
        "graph_node_label": (
            _display_label(
                node_record.get("graph_node_label"),
                str(node_record.get("raw_label") or "").strip(),
            )
            or None
        ),
        "graph_node_type": str(node_record.get("graph_node_type") or "").strip() or None,
        "source": str(node_record.get("source") or "graph").strip() or "graph",
        "label_kind": str(node_record.get("label_kind") or "").strip() or "specific",
        "entity_type": normalize_entity_type(str(node_record.get("entity_type") or "unknown")),
        "confidence": node_record.get("confidence"),
    }


def _build_confirmed_participant_ids_by_graph_node_id(
    confirmed_mentions: list[DossierParticipantMention],
) -> dict[str, set[int]]:
    lookup: dict[str, set[int]] = {}
    for mention in confirmed_mentions:
        participant_id = int(getattr(mention, "participant_id", 0) or 0)
        graph_node_id = str(getattr(mention, "graph_node_id", "") or "").strip()
        if participant_id <= 0 or not graph_node_id:
            continue
        lookup.setdefault(graph_node_id, set()).add(participant_id)
    return lookup


def _build_confirmed_participant_ids_by_legacy_label_key(
    confirmed_mentions: list[DossierParticipantMention],
) -> dict[tuple[int, str], set[int]]:
    lookup: dict[tuple[int, str], set[int]] = {}
    for mention in confirmed_mentions:
        participant_id = int(getattr(mention, "participant_id", 0) or 0)
        if participant_id <= 0:
            continue
        if str(getattr(mention, "graph_node_id", "") or "").strip():
            continue
        key = _resolution_node_legacy_label_key(
            {
                "conversation_id": int(getattr(mention, "conversation_id", 0) or 0),
                "raw_label": getattr(mention, "raw_label", ""),
            }
        )
        if key is None:
            continue
        lookup.setdefault(key, set()).add(participant_id)
    return lookup


def _lookup_participant_ids_for_resolution_node(
    node_record: dict[str, Any],
    *,
    confirmed_participant_ids_by_graph_node_id: dict[str, set[int]],
    confirmed_participant_ids_by_legacy_label_key: dict[tuple[int, str], set[int]],
) -> set[int]:
    graph_node_id = str(node_record.get("graph_node_id") or "").strip()
    if graph_node_id:
        participant_ids = set(confirmed_participant_ids_by_graph_node_id.get(graph_node_id, set()))
        if participant_ids:
            return participant_ids

    legacy_label_key = _resolution_node_legacy_label_key(node_record)
    if legacy_label_key is None:
        return set()
    return set(confirmed_participant_ids_by_legacy_label_key.get(legacy_label_key, set()))


def _node_type_pair_is_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_type = str(left.get("entity_type") or "unknown").strip() or "unknown"
    right_type = str(right.get("entity_type") or "unknown").strip() or "unknown"
    if left_type == "unknown" or right_type == "unknown":
        return True
    return left_type == right_type


def _normalized_resolution_graph_node_type(node_record: dict[str, Any]) -> str:
    return " ".join(str(node_record.get("graph_node_type") or "").strip().split()).lower()


def _is_non_resolvable_cross_conversation_node(node_record: dict[str, Any]) -> bool:
    graph_node_type = _normalized_resolution_graph_node_type(node_record)
    return graph_node_type in PARTICIPANT_NON_RESOLVABLE_GRAPH_NODE_TYPES


def _is_participant_resolvable_cross_conversation_node(
    node_record: dict[str, Any],
    *,
    participant_ids: set[int],
) -> bool:
    if _is_non_resolvable_cross_conversation_node(node_record):
        return False

    entity_type = str(node_record.get("entity_type") or "unknown").strip() or "unknown"
    if entity_type in PARTICIPANT_RESOLVABLE_ENTITY_TYPES:
        return True

    graph_node_type = _normalized_resolution_graph_node_type(node_record)
    if graph_node_type in PARTICIPANT_RESOLVABLE_UNKNOWN_GRAPH_NODE_TYPES:
        return True

    if entity_type == "unknown":
        return bool(participant_ids)

    return False


def _ordered_cross_resolution_nodes(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    left_participant_ids: set[int],
    right_participant_ids: set[int],
) -> tuple[dict[str, Any], dict[str, Any]]:
    left_kind = str(left.get("label_kind") or "specific")
    right_kind = str(right.get("label_kind") or "specific")
    left_has_participant = bool(left_participant_ids)
    right_has_participant = bool(right_participant_ids)

    if left_kind == "specific" and right_kind == "generic":
        return left, right
    if left_kind == "generic" and right_kind == "specific":
        return right, left
    if left_has_participant and not right_has_participant:
        return left, right
    if right_has_participant and not left_has_participant:
        return right, left

    left_identity = (
        int(left.get("conversation_id") or 0),
        str(left.get("graph_node_id") or ""),
        _normalized_label_key(left.get("raw_label")),
    )
    right_identity = (
        int(right.get("conversation_id") or 0),
        str(right.get("graph_node_id") or ""),
        _normalized_label_key(right.get("raw_label")),
    )
    if left_identity <= right_identity:
        return left, right
    return right, left


def _cross_resolution_pair_key(source_node: dict[str, Any], target_node: dict[str, Any]) -> str:
    source_conversation_id = int(source_node.get("conversation_id") or 0)
    target_conversation_id = int(target_node.get("conversation_id") or 0)
    source_node_key = str(source_node.get("graph_node_id") or _normalized_label_key(source_node.get("raw_label")) or "unknown")
    target_node_key = str(target_node.get("graph_node_id") or _normalized_label_key(target_node.get("raw_label")) or "unknown")
    return (
        f"cross-conversation:{source_conversation_id}:{source_node_key}:"
        f"{target_conversation_id}:{target_node_key}"
    )


def _build_cross_resolution_match_candidates(
    *,
    source_node: dict[str, Any],
    target_node: dict[str, Any],
    source_participant_ids: set[int],
    target_participant_ids: set[int],
    participants: list[DossierParticipant],
    participant_aliases: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    participant_lookup = {participant.id: participant for participant in participants}
    candidate_entries: list[dict[str, Any]] = []

    participant_ids = set(source_participant_ids) | set(target_participant_ids)

    if not participant_ids:
        for node in (source_node, target_node):
            exact_match, exact_match_type = _select_exact_candidate_participant(
                participants,
                raw_label=str(node.get("raw_label") or ""),
                participant_aliases=participant_aliases,
            )
            if exact_match is None:
                continue
            if exact_match_type == "exact_alias":
                reason = "该节点在当前案卷中与已确认别名精确匹配。"
            else:
                reason = "该节点在当前案卷中与已确认主体显示名精确匹配。"
            candidate_entries.append(
                _build_match_candidate_entry(
                    exact_match,
                    match_type=str(exact_match_type or "exact_label"),
                    reason=reason,
                )
            )
    else:
        for participant_id in sorted(participant_ids):
            participant = participant_lookup.get(participant_id)
            if participant is None:
                continue
            if participant_id in source_participant_ids and participant_id in target_participant_ids:
                reason = "该已确认主体已在两个会话节点上出现映射。"
            elif participant_id in source_participant_ids:
                reason = (
                    f"已有主体在其他会话中映射到 {source_node.get('graph_node_label') or source_node.get('raw_label') or '该节点'}。"
                )
            else:
                reason = (
                    f"已有主体在其他会话中映射到 {target_node.get('graph_node_label') or target_node.get('raw_label') or '该节点'}。"
                )
            candidate_entries.append(
                _build_match_candidate_entry(
                    participant,
                    match_type="confirmed_participant_mapping",
                    reason=reason,
                )
            )

    deduped_entries = _dedupe_match_candidates(candidate_entries)
    unique_participant_ids = {
        int(item.get("participant_id") or 0) for item in deduped_entries if int(item.get("participant_id") or 0) > 0
    }
    recommended_participant_id = next(iter(unique_participant_ids)) if len(unique_participant_ids) == 1 else None
    return deduped_entries, recommended_participant_id


def _build_cross_resolution_reason(
    *,
    source_node: dict[str, Any],
    target_node: dict[str, Any],
    confidence: str,
    recommended_participant_id: int | None,
) -> str:
    source_label = str(source_node.get("graph_node_label") or source_node.get("raw_label") or "该节点")
    target_label = str(target_node.get("graph_node_label") or target_node.get("raw_label") or "该节点")
    source_kind = str(source_node.get("label_kind") or "specific")
    target_kind = str(target_node.get("label_kind") or "specific")

    if confidence == "high":
        return f"不同会话中都出现了“{source_label}”，名称精确一致，可能指向同一实体，需要用户确认。"
    if source_kind != target_kind:
        return (
            f"不同会话中出现了“{source_label}”与“{target_label}”，其中一个是泛称、一个是具体节点，"
            "可能指向同一实体，需要用户确认。"
        )
    if recommended_participant_id is not None:
        return "已有主体在其他会话中已经映射到其中一个节点，当前节点可能与之属于同一实体。"
    return f"不同会话中的“{source_label}”与“{target_label}”可能是同一实体，需要用户确认。"


def _build_cross_conversation_entity_candidate(
    *,
    source_node: dict[str, Any],
    target_node: dict[str, Any],
    source_participant_ids: set[int],
    target_participant_ids: set[int],
    participants: list[DossierParticipant],
    participant_aliases: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    source_kind = str(source_node.get("label_kind") or "specific")
    target_kind = str(target_node.get("label_kind") or "specific")
    source_label = _normalized_label_key(source_node.get("raw_label"))
    target_label = _normalized_label_key(target_node.get("raw_label"))

    exact_same_label = bool(source_label and source_label == target_label)
    has_participant_evidence = bool(source_participant_ids or target_participant_ids)
    one_generic_one_specific = {source_kind, target_kind} == {"generic", "specific"}
    both_generic = source_kind == "generic" and target_kind == "generic"

    if both_generic:
        return None
    if exact_same_label:
        confidence = "high"
    elif one_generic_one_specific and has_participant_evidence:
        confidence = "medium"
    else:
        return None

    match_candidates, recommended_participant_id = _build_cross_resolution_match_candidates(
        source_node=source_node,
        target_node=target_node,
        source_participant_ids=source_participant_ids,
        target_participant_ids=target_participant_ids,
        participants=participants,
        participant_aliases=participant_aliases,
    )

    if one_generic_one_specific and recommended_participant_id is None:
        return None

    participant_lookup = {participant.id: participant for participant in participants}
    recommended_participant = participant_lookup.get(recommended_participant_id or 0)

    if one_generic_one_specific:
        generic_node = source_node if source_kind == "generic" else target_node
        specific_node = target_node if source_kind == "generic" else source_node
        raw_label = str(generic_node.get("raw_label") or "").strip()
        suggested_label = (
            recommended_participant.label
            if recommended_participant is not None
            else str(specific_node.get("graph_node_label") or specific_node.get("raw_label") or "").strip()
        )
        label_kind = "mixed"
    else:
        raw_label = str(source_node.get("raw_label") or "").strip()
        suggested_label = (
            recommended_participant.label
            if recommended_participant is not None
            else str(source_node.get("graph_node_label") or source_node.get("raw_label") or "").strip()
        )
        label_kind = source_kind if source_kind == target_kind else "mixed"

    can_create_new = (
        recommended_participant_id is None
        and confidence == "high"
        and source_kind == "specific"
        and target_kind == "specific"
        and bool(suggested_label)
    )

    candidate_key = _cross_resolution_pair_key(source_node, target_node)
    reason = _build_cross_resolution_reason(
        source_node=source_node,
        target_node=target_node,
        confidence=confidence,
        recommended_participant_id=recommended_participant_id,
    )
    serialized_source_node = _serialize_resolution_node_record(source_node)
    serialized_target_node = _serialize_resolution_node_record(target_node)
    mentions = [serialized_source_node, serialized_target_node]

    return {
        "candidate_key": candidate_key,
        "task_key": candidate_key,
        "task_type": "cross_conversation_entity_resolution",
        "label_kind": label_kind,
        "raw_label": raw_label,
        "raw_labels": list(
            dict.fromkeys(
                [
                    str(source_node.get("raw_label") or "").strip(),
                    str(target_node.get("raw_label") or "").strip(),
                ]
            )
        ),
        "suggested_label": suggested_label,
        "reason": reason,
        "confidence": confidence,
        "source_node": serialized_source_node,
        "target_node": serialized_target_node,
        "mentions": mentions,
        "match_candidates": match_candidates,
        "existing_participant_id": recommended_participant_id,
        "existing_participant_label": (
            recommended_participant.label if recommended_participant is not None else ""
        ),
        "recommended_participant_id": recommended_participant_id,
        "recommended_participant_label": (
            recommended_participant.label if recommended_participant is not None else ""
        ),
        "can_link_existing": bool(participants),
        "can_create_new": can_create_new,
        "can_mark_non_entity": False,
        "can_mark_not_same": True,
        "can_skip": True,
    }


def _build_cross_conversation_entity_candidates(
    *,
    graph_node_records: list[dict[str, Any]],
    participants: list[DossierParticipant],
    confirmed_mentions: list[DossierParticipantMention],
    participant_aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if len(graph_node_records) < 2:
        return []

    confirmed_participant_ids_by_graph_node_id = _build_confirmed_participant_ids_by_graph_node_id(
        confirmed_mentions
    )
    confirmed_participant_ids_by_legacy_label_key = (
        _build_confirmed_participant_ids_by_legacy_label_key(confirmed_mentions)
    )
    node_participant_ids: dict[str, set[int]] = {}
    for node_record in graph_node_records:
        candidate_key = _cross_resolution_pair_key(node_record, node_record)
        node_participant_ids[candidate_key] = _lookup_participant_ids_for_resolution_node(
            node_record,
            confirmed_participant_ids_by_graph_node_id=confirmed_participant_ids_by_graph_node_id,
            confirmed_participant_ids_by_legacy_label_key=confirmed_participant_ids_by_legacy_label_key,
        )

    candidates: list[dict[str, Any]] = []
    seen_candidate_keys: set[str] = set()
    for index, left in enumerate(graph_node_records):
        left_self_key = _cross_resolution_pair_key(left, left)
        left_participant_ids = node_participant_ids.get(left_self_key, set())
        for right in graph_node_records[index + 1 :]:
            if int(left.get("conversation_id") or 0) == int(right.get("conversation_id") or 0):
                continue
            right_self_key = _cross_resolution_pair_key(right, right)
            right_participant_ids = node_participant_ids.get(right_self_key, set())
            if not _is_participant_resolvable_cross_conversation_node(
                left,
                participant_ids=left_participant_ids,
            ):
                continue
            if not _is_participant_resolvable_cross_conversation_node(
                right,
                participant_ids=right_participant_ids,
            ):
                continue
            if not _node_type_pair_is_compatible(left, right):
                continue
            if left_participant_ids and right_participant_ids:
                continue

            source_node, target_node = _ordered_cross_resolution_nodes(
                left,
                right,
                left_participant_ids=left_participant_ids,
                right_participant_ids=right_participant_ids,
            )
            if source_node is left:
                source_participant_ids = left_participant_ids
                target_participant_ids = right_participant_ids
            else:
                source_participant_ids = right_participant_ids
                target_participant_ids = left_participant_ids

            candidate = _build_cross_conversation_entity_candidate(
                source_node=source_node,
                target_node=target_node,
                source_participant_ids=source_participant_ids,
                target_participant_ids=target_participant_ids,
                participants=participants,
                participant_aliases=participant_aliases,
            )
            if candidate is None:
                continue
            candidate_key = str(candidate.get("candidate_key") or "").strip()
            if not candidate_key or candidate_key in seen_candidate_keys:
                continue
            seen_candidate_keys.add(candidate_key)
            candidates.append(candidate)
    return candidates


def build_dossier_participant_candidates(
    *,
    graph_payload: dict[str, Any] | None,
    participants: list[DossierParticipant],
    confirmed_mentions: list[DossierParticipantMention],
    participant_aliases: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    graph = graph_payload.get("graph") if isinstance(graph_payload, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph, dict) else []
    if not isinstance(nodes, list):
        return []

    normalized_aliases = _normalize_participant_aliases(
        participant_aliases if isinstance(participant_aliases, dict) else None
    )

    graph_node_records: list[dict[str, Any]] = []
    for node in nodes:
        normalized_node = _build_resolution_node_record(
            node if isinstance(node, dict) else {},
            participant_aliases=normalized_aliases,
        )
        if normalized_node is None:
            continue
        graph_node_records.append(normalized_node)

    confirmed_graph_node_ids = {
        str(getattr(mention, "graph_node_id", "") or "").strip()
        for mention in confirmed_mentions
        if str(getattr(mention, "graph_node_id", "") or "").strip()
    }
    confirmed_legacy_label_keys = {
        (
            int(getattr(mention, "conversation_id", 0) or 0),
            _normalized_label_key(getattr(mention, "raw_label", "")),
        )
        for mention in confirmed_mentions
        if not str(getattr(mention, "graph_node_id", "") or "").strip()
        if int(getattr(mention, "conversation_id", 0) or 0) > 0
        and _normalized_label_key(getattr(mention, "raw_label", ""))
    }

    unresolved_mentions: list[dict[str, Any]] = []
    for node_record in graph_node_records:
        graph_node_id = str(node_record.get("graph_node_id") or "").strip()
        conversation_id = int(node_record.get("conversation_id") or 0)
        raw_label = str(node_record.get("raw_label") or "").strip()
        if graph_node_id and graph_node_id in confirmed_graph_node_ids:
            continue
        if (conversation_id, _normalized_label_key(raw_label)) in confirmed_legacy_label_keys:
            continue
        unresolved_mentions.append(node_record)

    if not unresolved_mentions:
        return _build_cross_conversation_entity_candidates(
            graph_node_records=graph_node_records,
            participants=participants,
            confirmed_mentions=confirmed_mentions,
            participant_aliases=normalized_aliases,
        )

    task_candidates: list[dict[str, Any]] = []
    task_mentions_by_label: dict[str, list[dict[str, Any]]] = {}
    for mention in unresolved_mentions:
        task_mentions_by_label.setdefault(_normalized_label_key(mention["raw_label"]), []).append(mention)

    for normalized_label, mentions in task_mentions_by_label.items():
        if not normalized_label or not mentions:
            continue
        raw_label = str(mentions[0].get("raw_label") or "").strip()
        if not raw_label:
            continue
        candidate = _build_candidate_payload(
            candidate_key=f"label:{normalized_label}",
            raw_label=raw_label,
            mentions=mentions,
            participants=participants,
            participant_aliases=normalized_aliases,
        )
        if candidate is not None:
            task_candidates.append(candidate)

    cross_conversation_candidates = _build_cross_conversation_entity_candidates(
        graph_node_records=graph_node_records,
        participants=participants,
        confirmed_mentions=confirmed_mentions,
        participant_aliases=normalized_aliases,
    )

    combined_candidates = cross_conversation_candidates + task_candidates
    combined_candidates.sort(
        key=lambda item: (
            0 if str(item.get("task_type") or "") == "cross_conversation_entity_resolution" else 1,
            0 if str(item.get("label_kind") or "") in {"specific", "mixed"} else 1,
            str(item.get("raw_label") or ""),
        )
    )
    return combined_candidates


def serialize_dossier(dossier: Dossier) -> dict[str, Any]:
    participant_aliases = _load_participant_aliases(
        getattr(dossier, "participant_aliases_json", None)
    )
    return {
        "id": dossier.id,
        "user_id": dossier.user_id,
        "title": dossier.title,
        "case_type": dossier.case_type or "",
        "summary": dossier.summary or "",
        "auto_summary": getattr(dossier, "auto_summary", None) or "",
        "auto_summary_updated_at": (
            dossier.auto_summary_updated_at.isoformat()
            if getattr(dossier, "auto_summary_updated_at", None)
            else None
        ),
        "auto_summary_source_count": int(getattr(dossier, "auto_summary_source_count", 0) or 0),
        "participant_aliases": participant_aliases,
        "status": normalize_dossier_status(getattr(dossier, "status", DEFAULT_DOSSIER_STATUS)),
        "created_at": dossier.created_at.isoformat() if dossier.created_at else None,
        "updated_at": dossier.updated_at.isoformat() if dossier.updated_at else None,
    }


def serialize_dossier_conversation_summary(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "conversation_mode": normalize_conversation_mode(
            getattr(conversation, "conversation_mode", DEFAULT_CONVERSATION_MODE)
        ),
        "persistence_consent": normalize_persistence_consent(
            getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
        ),
        "case_type": getattr(conversation, "case_type", None) or "",
        "case_summary": (getattr(conversation, "case_summary", None) or "").strip(),
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def serialize_dossier_detail(
    dossier: Dossier,
    conversations: list[Conversation],
    confirmed_participants: list[DossierParticipant] | None = None,
    participant_mentions: list[DossierParticipantMention] | None = None,
) -> dict[str, Any]:
    payload = serialize_dossier(dossier)
    payload["conversation_count"] = len(conversations)
    payload["conversations"] = [
        serialize_dossier_conversation_summary(conversation)
        for conversation in conversations
    ]
    participant_list = confirmed_participants or []
    mention_list = participant_mentions or []
    participant_lookup = build_dossier_participant_lookup(participant_list)
    payload["confirmed_participants"] = [
        serialize_dossier_participant(participant)
        for participant in participant_list
    ]
    payload["participant_mentions"] = [
        serialize_dossier_participant_mention(mention, participant_lookup)
        for mention in mention_list
    ]
    return payload


async def load_dossier_context_for_conversation(
    db: AsyncSession,
    user_id: int,
    conversation_id: int,
) -> dict[str, Any] | None:
    conversation = await get_conversation_for_user(db, user_id, conversation_id)
    if conversation is None:
        return None

    dossier_id = getattr(conversation, "dossier_id", None)
    if dossier_id is None:
        return None

    dossier = await get_dossier_for_user(db, user_id, dossier_id)
    if dossier is None:
        return None

    conversations = await list_dossier_conversations(db, user_id, dossier.id)
    participants = await list_dossier_participants(db, user_id, dossier.id)
    mentions = await list_dossier_participant_mentions(db, user_id, dossier.id)
    payload = serialize_dossier_detail(
        dossier,
        conversations,
        confirmed_participants=participants,
        participant_mentions=mentions,
    )
    payload["active_conversation_id"] = conversation.id
    return payload


def serialize_conversation(conversation: Conversation) -> dict[str, Any]:
    conversation_mode = normalize_conversation_mode(
        getattr(conversation, "conversation_mode", DEFAULT_CONVERSATION_MODE)
    )
    persistence_consent = normalize_persistence_consent(
        getattr(conversation, "persistence_consent", DEFAULT_PERSISTENCE_CONSENT)
    )
    return {
        "id": conversation.id,
        "user_id": conversation.user_id,
        "dossier_id": getattr(conversation, "dossier_id", None),
        "session_id": conversation.session_id,
        "title": conversation.title,
        "case_summary": conversation.case_summary or "",
        "case_type": getattr(conversation, "case_type", None) or "",
        "case_state": getattr(conversation, "case_state_json", None),
        "conversation_mode": conversation_mode,
        "persistence_consent": persistence_consent,
        "mode_confirmed_at": (
            conversation.mode_confirmed_at.isoformat()
            if getattr(conversation, "mode_confirmed_at", None)
            else None
        ),
        "mode_reason": getattr(conversation, "mode_reason", None),
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
    }


def serialize_message(message: Message) -> dict[str, Any]:
    metadata = message.message_metadata or {}
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "role": message.role,
        "content": message.content,
        "metadata": metadata,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }
