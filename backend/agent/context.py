"""Context assembly from mode prompt, rules, book context, snippets, and history."""

from __future__ import annotations

import sqlite3

from backend.agent.prompts import resolve_mode_prompt
from backend.agent.serialization import message_to_llm
from backend.llm.types import LLMMessage, Role
from backend.storage.models import Book, Session
from backend.storage.repos.config import get_setting
from backend.storage.repos.sessions import list_messages

GLOBAL_RULES_SETTING_KEY = "global_rules_markdown"


def build_context(
    conn: sqlite3.Connection,
    *,
    session: Session,
    book: Book,
    extra_snippets: tuple[str, ...] = (),
) -> tuple[LLMMessage, ...]:
    """Build the ordered LLMMessage sequence for one agent iteration."""
    sections: list[str] = [resolve_mode_prompt(conn, session.mode)]

    global_rules = (get_setting(conn, GLOBAL_RULES_SETTING_KEY) or "").strip()
    if global_rules:
        sections.append(f"## 全局写作规则\n\n{global_rules}")

    book_lines = [f"## 当前作品：{book.title}"]
    if book.description.strip():
        book_lines.append(f"简介：{book.description.strip()}")
    if book.rules_markdown.strip():
        book_lines.append(f"### 本书规则\n\n{book.rules_markdown.strip()}")
    sections.append("\n\n".join(book_lines))

    clean_snippets = [s.strip() for s in extra_snippets if s.strip()]
    if clean_snippets:
        joined_snippets = "\n\n---\n\n".join(clean_snippets)
        sections.append(f"## 本轮参考片段\n\n{joined_snippets}")

    system_message = LLMMessage.text(Role.SYSTEM, "\n\n".join(sections))
    history = [message_to_llm(msg) for msg in list_messages(conn, session.id)]
    return (system_message, *history)
