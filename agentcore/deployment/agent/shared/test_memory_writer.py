"""
Unit tests for the runtime's single memory writer.

Run from agentcore/deployment/agent/:
    python3 -m unittest discover -s shared -p 'test_memory_writer.py' -v

The tests run without strands or bedrock_agentcore installed: the manager
module guards those imports, and the two memory types it uses are replaced
with small fakes.
"""

import os
import re
import unittest
from unittest import mock

import agentcore_memory_conversation_manager as manager_module
from memory_text import persistable_text


class FakeMessageRole:
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class FakeConversationalMessage:
    def __init__(self, text, role):
        self.text = text
        self.role = role


class FakeMemorySession:
    def __init__(self):
        self.add_turns_calls = []

    def add_turns(self, messages):
        self.add_turns_calls.append(list(messages))


class FakeAgent:
    def __init__(self, messages):
        self.messages = messages


def tool_use_block():
    return {"toolUse": {"toolUseId": "tooluse_abc123", "name": "lookup", "input": {"q": "ctv"}}}


def tool_result_block():
    return {
        "toolResult": {
            "toolUseId": "tooluse_abc123",
            "status": "success",
            "content": [{"text": "42 placements"}],
        }
    }


def make_manager():
    manager = manager_module.AgentCoreMemoryConversationManager(
        memory_id="mem-test-0001",
        actor_id="AgencyAgent",
        session_id="0123456789abcdef0123456789abcdef0",
        region_name="us-east-1",
    )
    session = FakeMemorySession()
    manager._memory_session = session
    manager._memory_available = True
    return manager, session


class PersistableTextTest(unittest.TestCase):
    def test_persistable_text_skips_tool_and_reasoning_blocks(self):
        mixed = {
            "role": "assistant",
            "content": [{"text": "Checking inventory."}, tool_use_block()],
        }
        tool_result_only = {"role": "user", "content": [tool_result_block()]}
        reasoning_only = {
            "role": "assistant",
            "content": [{"reasoningContent": {"reasoningText": {"text": "thinking"}}}],
        }

        self.assertEqual(persistable_text(mixed), "Checking inventory.")
        self.assertIsNone(persistable_text(tool_result_only))
        self.assertIsNone(persistable_text(reasoning_only))
        self.assertIsNone(persistable_text({"role": "user", "content": [{"text": "   "}]}))


class ApplyManagementTest(unittest.TestCase):
    def test_apply_management_writes_one_event_per_text_message(self):
        manager, session = make_manager()
        agent = FakeAgent(
            [
                {"role": "user", "content": [{"text": "Plan a CTV campaign"}]},
                {"role": "assistant", "content": [tool_use_block()]},
                {"role": "user", "content": [tool_result_block()]},
                {"role": "assistant", "content": [{"text": "Here is the plan."}]},
            ]
        )

        with mock.patch.object(
            manager_module, "ConversationalMessage", FakeConversationalMessage, create=True
        ), mock.patch.object(manager_module, "MessageRole", FakeMessageRole, create=True):
            manager.apply_management(agent)
            # A second call with no new messages must not write again.
            manager.apply_management(agent)

        self.assertEqual(len(session.add_turns_calls), 1)
        persisted = [(m.role, m.text) for m in session.add_turns_calls[0]]
        self.assertEqual(
            persisted,
            [("USER", "Plan a CTV campaign"), ("ASSISTANT", "Here is the plan.")],
        )
        self.assertEqual(manager._last_persisted_index, 3)


class RestoreFromSessionTest(unittest.TestCase):
    def test_restore_returns_the_text_turns_the_writer_stored(self):
        manager, session = make_manager()
        session.get_last_k_turns = mock.Mock(
            return_value=[
                [
                    {"role": "USER", "content": {"text": "Plan a CTV campaign"}},
                    {"role": "ASSISTANT", "content": {"text": "Here is the plan."}},
                ]
            ]
        )

        restored = manager.restore_from_session({})

        self.assertEqual(
            restored,
            [
                {"role": "user", "content": [{"text": "Plan a CTV campaign"}]},
                {"role": "assistant", "content": [{"text": "Here is the plan."}]},
            ],
        )

    def test_restore_returns_none_when_memory_has_no_turns(self):
        manager, session = make_manager()
        session.get_last_k_turns = mock.Mock(return_value=[])

        self.assertIsNone(manager.restore_from_session({}))


class HandlerWriterTest(unittest.TestCase):
    def test_handler_attaches_no_short_term_memory_hook(self):
        handler_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "handler.py")
        with open(handler_path, encoding="utf-8") as handler_file:
            source = handler_file.read()

        code_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
        instantiations = [line for line in code_lines if re.search(r"ShortTermMemoryHook\s*\(", line)]
        self.assertEqual(instantiations, [], "handler.py still instantiates ShortTermMemoryHook")


if __name__ == "__main__":
    unittest.main()
