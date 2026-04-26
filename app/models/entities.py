from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.mysql_db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    dossiers: Mapped[list["Dossier"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Dossier(Base):
    __tablename__ = "dossiers"
    __table_args__ = (
        Index("ix_dossiers_user_id_status", "user_id", "status"),
        Index("ix_dossiers_updated_at", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    case_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default=None,
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    auto_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    auto_summary_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
    )
    auto_summary_source_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    participant_aliases_json: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    user: Mapped[User] = relationship(back_populates="dossiers")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="dossier")
    participants: Mapped[list["DossierParticipant"]] = relationship(
        back_populates="dossier",
        cascade="all, delete-orphan",
    )
    participant_mentions: Mapped[list["DossierParticipantMention"]] = relationship(
        back_populates="dossier",
        cascade="all, delete-orphan",
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id_dossier_id", "user_id", "dossier_id"),
        Index("ix_conversations_user_id_updated_at", "user_id", "updated_at"),
        Index("ix_conversations_conversation_mode", "conversation_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dossier_id: Mapped[int | None] = mapped_column(
        ForeignKey("dossiers.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )
    session_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    case_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_state_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
        comment="Serialized CaseState snapshot",
    )
    case_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        default=None,
        comment="Case type label",
    )
    conversation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="UNKNOWN",
        server_default="UNKNOWN",
        comment="Conversation mode: UNKNOWN/LEGAL_QA/CASE_ANALYSIS/TRACKED_CASE",
    )
    persistence_consent: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        server_default="unknown",
        comment="Persistence consent: unknown/temporary/tracked",
    )
    mode_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
    )
    mode_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    user: Mapped[User] = relationship(back_populates="conversations")
    dossier: Mapped[Dossier | None] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )
    participant_mentions: Mapped[list["DossierParticipantMention"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class DossierParticipant(Base):
    __tablename__ = "dossier_participants"
    __table_args__ = (
        Index("ix_dossier_participants_dossier_id_label", "dossier_id", "label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    role: Mapped[str | None] = mapped_column(String(100), nullable=True, default=None)
    party_side: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    entity_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    confirmed_by_user: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    dossier: Mapped[Dossier] = relationship(back_populates="participants")
    mentions: Mapped[list["DossierParticipantMention"]] = relationship(
        back_populates="participant",
        cascade="all, delete-orphan",
    )


class DossierParticipantMention(Base):
    __tablename__ = "dossier_participant_mentions"
    __table_args__ = (
        Index(
            "ix_dossier_participant_mentions_dossier_id_raw_label",
            "dossier_id",
            "raw_label",
        ),
        Index(
            "ix_dossier_participant_mentions_dossier_id_conversation_id",
            "dossier_id",
            "conversation_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dossier_id: Mapped[int] = mapped_column(
        ForeignKey("dossiers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("dossier_participants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    raw_label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="graph",
        server_default="graph",
    )
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    confirmed_by_user: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    dossier: Mapped[Dossier] = relationship(back_populates="participant_mentions")
    conversation: Mapped[Conversation] = relationship(back_populates="participant_mentions")
    participant: Mapped[DossierParticipant] = relationship(back_populates="mentions")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
