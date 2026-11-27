"""Precise patching, metadata inheritance and visible pending proposal revision."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from backend.agent.edits import TextEdit, apply_edits
from backend.agent.pending import revision_token
from backend.agent.tools import (
    LIST_PENDING_CHANGES_TOOL,
    PROPOSE_CHAPTER_EDIT_TOOL,
    PROPOSE_ENTRY_EDIT_TOOL,
    READ_PENDING_CHANGE_TOOL,
    REVISE_PENDING_CHANGE_TOOL,
    ToolContext,
    tools_for_mode,
)
from backend.storage.db import Database
from backend.storage.models import EntryKind, PendingStatus, SessionMode, Visibility
from backend.storage.repos.changes import (
    apply_pending_change,
    get_pending_change,
    reject_pending_change,
)
from backend.storage.repos.content import create_book, create_chapter, create_entry, get_entry
from backend.storage.repos.sessions import create_run, create_session


@contextmanager
def tool_workspace(
    scope: Visibility = Visibility.GLOBAL, mode: SessionMode = SessionMode.WRITE
) -> Iterator[ToolContext]:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "20261127-edit-tools"
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root) as directory:
        db = Database(Path(directory) / "inkwell.db")
        db.initialize()
        with db.transaction() as conn:
            book = create_book(conn, title="Book")
            session = create_session(conn, book_id=book.id, mode=mode, visibility_scope=scope)
            run = create_run(conn, book_id=book.id, session_id=session.id)
        yield ToolContext(db, book.id, session.id, run.id, scope)


def test_precise_and_fuzzy_edits_preserve_unmodified_source() -> None:
    source = "不动：Ａ，Ｂ。  \r\n“甲”\u00a0—\u3000乙  \r\n末尾。\r\n"
    result = apply_edits(source, [TextEdit(search='"甲" - 乙\n', replace="改稿\r\n")])
    assert result == "不动：Ａ，Ｂ。  \r\n改稿\r\n末尾。\r\n"
    assert apply_edits('“甲” 和 "甲"', [TextEdit(search='"甲"', replace="乙")]) == "“甲” 和 乙"
    with pytest.raises(ValueError, match="ambiguous"):
        apply_edits("aaa", [TextEdit(search="aa", replace="b")])
    with pytest.raises(ValueError, match="not found"):
        apply_edits("Ａ，Ｂ。", [TextEdit(search="A,B。", replace="bad")])
    with pytest.raises(ValueError, match="ambiguous"):
        apply_edits("“甲” „甲‟", [TextEdit(search='"甲"', replace="x")])


def test_entry_edit_inherits_metadata_and_keeps_pending_baseline() -> None:
    with tool_workspace() as ctx:
        with ctx.db.transaction() as conn:
            entry = create_entry(
                conn,
                book_id=ctx.book_id,
                kind=EntryKind.OUTLINE,
                title="提纲",
                category="卷一",
                content="原文\n保留。",
                visibility=Visibility.GLOBAL,
                meta_json='{"tags":["主线"]}',
                sort_order=19,
            )
        result = PROPOSE_ENTRY_EDIT_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "action": "update",
                    "target_id": entry.id,
                    "edits": [{"search": "原文", "replace": "新版"}],
                }
            ),
        )
        assert not result.is_error and result.created_change is not None
        change = result.created_change
        assert change.title == entry.title and change.before_content == entry.content
        assert change.after_content == "新版\n保留。"
        payload = json.loads(change.payload_json)
        assert payload == {
            "kind": "outline",
            "category": "卷一",
            "visibility": "global",
            "meta_json": entry.meta_json,
            "sort_order": 19,
        }
        invalid = PROPOSE_ENTRY_EDIT_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "action": "update",
                    "target_id": entry.id,
                    "content": "oops",
                    "edits": [{"search": "原文", "replace": "改"}],
                }
            ),
        )
        assert invalid.is_error
        with ctx.db.transaction() as conn:
            apply_pending_change(conn, change.id)
            updated = get_entry(conn, entry.id)
            assert updated is not None and updated.visibility == Visibility.GLOBAL
            assert updated.category == "卷一" and updated.content == change.after_content


def test_pending_revision_visibility_conflicts_and_mode_permissions() -> None:
    with tool_workspace() as ctx:
        with ctx.db.transaction() as conn:
            entry = create_entry(
                conn,
                book_id=ctx.book_id,
                kind=EntryKind.NOTE,
                title="秘密",
                content="原文",
                visibility=Visibility.GLOBAL,
            )
            chapter = create_chapter(conn, book_id=ctx.book_id, title="章", content="甲")
            local = create_session(
                conn,
                book_id=ctx.book_id,
                mode=SessionMode.DISCUSS,
                visibility_scope=Visibility.LOCAL,
            )
            local_run = create_run(conn, book_id=ctx.book_id, session_id=local.id)
        result = PROPOSE_ENTRY_EDIT_TOOL.execute(
            ctx, json.dumps({"action": "update", "target_id": entry.id, "content": "草案"})
        )
        change = result.created_change
        assert change is not None
        local_ctx = ToolContext(ctx.db, ctx.book_id, local.id, local_run.id, Visibility.LOCAL)
        assert READ_PENDING_CHANGE_TOOL.execute(
            local_ctx, json.dumps({"change_id": change.id})
        ).is_error
        listed = LIST_PENDING_CHANGES_TOOL.execute(local_ctx, '{"scope":"book"}')
        assert json.loads(listed.content)["data"]["changes"] == []
        with ctx.db.transaction() as conn:
            public = create_entry(
                conn, book_id=ctx.book_id, kind=EntryKind.NOTE, title="公开", content="公开原文"
            )
            foreign_book = create_book(conn, title="另一书")
            foreign_session = create_session(conn, book_id=foreign_book.id)
            foreign_run = create_run(conn, book_id=foreign_book.id, session_id=foreign_session.id)
        hidden_draft = PROPOSE_ENTRY_EDIT_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "action": "update",
                    "target_id": public.id,
                    "content": "仅全局草案",
                    "visibility": "global",
                }
            ),
        ).created_change
        assert hidden_draft is not None
        assert READ_PENDING_CHANGE_TOOL.execute(
            local_ctx, json.dumps({"change_id": hidden_draft.id})
        ).is_error
        foreign_ctx = ToolContext(
            ctx.db, foreign_book.id, foreign_session.id, foreign_run.id, Visibility.GLOBAL
        )
        assert READ_PENDING_CHANGE_TOOL.execute(
            foreign_ctx, json.dumps({"change_id": change.id})
        ).is_error
        assert PROPOSE_ENTRY_EDIT_TOOL.execute(
            local_ctx,
            json.dumps({"action": "update", "target_id": public.id, "visibility": "global"}),
        ).is_error
        revised = REVISE_PENDING_CHANGE_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "change_id": change.id,
                    "expected_revision": revision_token(change),
                    "edits": [{"search": "草案", "replace": "微调"}],
                }
            ),
        )
        assert not revised.is_error and revised.updated_change is not None
        assert (
            revised.updated_change.id == change.id
            and revised.updated_change.before_content == "原文"
        )
        stale = REVISE_PENDING_CHANGE_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "change_id": change.id,
                    "expected_revision": revision_token(change),
                    "content": "过期",
                }
            ),
        )
        assert stale.is_error and "conflict" in stale.content
        with ctx.db.transaction() as conn:
            reject_pending_change(conn, change.id)
        resolved = REVISE_PENDING_CHANGE_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "change_id": change.id,
                    "expected_revision": revision_token(revised.updated_change),
                    "content": "错误",
                }
            ),
        )
        assert resolved.is_error
        with ctx.db.transaction() as conn:
            final = get_pending_change(conn, change.id)
            assert (
                final is not None
                and final.status == PendingStatus.REJECTED
                and final.after_content == "微调"
            )
        chapter_result = PROPOSE_CHAPTER_EDIT_TOOL.execute(
            ctx,
            json.dumps(
                {
                    "action": "update",
                    "target_id": chapter.id,
                    "edits": [{"search": "甲", "replace": "乙"}],
                }
            ),
        )
        chapter_change = chapter_result.created_change
        assert chapter_change is not None
        assert REVISE_PENDING_CHANGE_TOOL.execute(
            local_ctx,
            json.dumps(
                {
                    "change_id": chapter_change.id,
                    "expected_revision": revision_token(chapter_change),
                    "content": "无权限",
                }
            ),
        ).is_error
        assert "revise_pending_change" not in {
            tool.name for tool in tools_for_mode(SessionMode.REVIEW)
        }
