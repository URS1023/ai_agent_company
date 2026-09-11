"""Native lookup SQL must scope both conversation and parent to the signed-in account."""

from importlib import import_module
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from constants import UUID_NIL
from models import Account
from models.model import App, Conversation, Message
from services.errors.conversation import ConversationNotExistsError
from services.errors.message import MessageNotExistsError


@pytest.fixture
def context():
    check = import_module("services.workbench_context_service").require_workbench_context
    app = App(id="app")
    user = Account(name="User", email="user@example.test")
    user.id = "actor"
    session = Mock(spec=Session)
    return check, app, user, session


def check_context(context, conversation_id="conversation", parent_message_id="parent"):
    check, app, user, session = context
    return check(
        app_model=app, user=user, session=session, conversation_id=conversation_id, parent_message_id=parent_message_id
    )


def test_existing_native_queries_scope_both_reads_and_do_not_write(context):
    _, _, _, session = context
    conversation = Conversation(id="conversation")
    session.scalar.side_effect = [conversation, Message(id="parent", conversation_id="conversation")]
    assert check_context(context) is conversation
    assert len(session.scalar.call_args_list) == 2
    for call in session.scalar.call_args_list:
        compiled = call.args[0].compile(dialect=postgresql.dialect())
        assert "app" in compiled.params.values()
        assert "actor" in compiled.params.values()
        assert "console" in compiled.params.values()
        assert "from_end_user_id IS NULL" in str(compiled)
    assert "is_deleted = false" in str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
    session.commit.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize("parent", [None, "", UUID_NIL])
def test_new_conversation_with_root_parent_needs_no_history_read(context, parent):
    assert check_context(context, None, parent) is None
    context[3].scalar.assert_not_called()


def test_missing_conversation_stops_before_parent_read(context):
    context[3].scalar.return_value = None
    with pytest.raises(ConversationNotExistsError):
        check_context(context)
    assert context[3].scalar.call_count == 1


def test_missing_or_inaccessible_parent_is_rejected(context):
    context[3].scalar.side_effect = [Conversation(id="conversation"), None]
    with pytest.raises(MessageNotExistsError):
        check_context(context)


def test_parent_from_different_conversation_is_rejected(context):
    context[3].scalar.side_effect = [Conversation(id="conversation"), Message(conversation_id="another")]
    with pytest.raises(MessageNotExistsError):
        check_context(context)


def test_parent_without_conversation_is_rejected_without_lookup(context):
    with pytest.raises(MessageNotExistsError):
        check_context(context, None, "parent")
    context[3].scalar.assert_not_called()
