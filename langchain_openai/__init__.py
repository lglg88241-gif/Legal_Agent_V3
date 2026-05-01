"""Minimal local compatibility layer for `langchain_openai`."""
from __future__ import annotations

from app.core.llm_factory import ChatOpenAI

__all__ = ["ChatOpenAI"]
