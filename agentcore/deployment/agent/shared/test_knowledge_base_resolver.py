"""
Unit tests for knowledge base resolution without the KNOWLEDGEBASES env var.
Run from agentcore/deployment/agent/ with a Python that has boto3 (for example
the repo's .venv-deployment):
    python -m unittest discover -s shared -p 'test_knowledge_base_resolver.py' -v
strands is stubbed in sys.modules so the tests run without it installed, and
the Bedrock Agent client is a fake that returns two pages of knowledge bases.
"""
import os
import sys
import types
import unittest
from unittest import mock


def _stub_strands():
    strands = types.ModuleType("strands")
    strands.Agent = object
    strands.tool = lambda fn=None, **_: fn if fn else (lambda f: f)
    tools = types.ModuleType("strands_tools")
    tools.http_request = tools.use_llm = tools.memory = object()
    sys.modules.setdefault("strands", strands)
    sys.modules.setdefault("strands_tools", tools)


_stub_strands()
import knowledge_base_helper as kbh  # noqa: E402


class FakeBedrockAgentClient:
    """Two pages of list_knowledge_bases results."""

    def __init__(self):
        self.calls = []

    def list_knowledge_bases(self, **kwargs):
        self.calls.append(kwargs)
        if "nextToken" not in kwargs:
            return {
                "knowledgeBaseSummaries": [
                    {"name": "ts3-Advertising-rhd1h0", "knowledgeBaseId": "2WG26EVR2Z"},
                ],
                "nextToken": "page-2",
            }
        return {
            "knowledgeBaseSummaries": [
                {"name": "partner-media-kb", "knowledgeBaseId": "58ZLOFOJXN"},
            ]
        }


class ResolveKnowledgeBaseIdTest(unittest.TestCase):
    def setUp(self):
        kbh._KB_NAME_CACHE.clear()
        self.client = FakeBedrockAgentClient()
        patcher = mock.patch.object(
            kbh.KnowledgeBaseHelper, "_get_bedrock_agent_client", return_value=self.client
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(
            os.environ, {"STACK_PREFIX": "ts3", "UNIQUE_ID": "rhd1h0"}, clear=False
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("KNOWLEDGEBASES", None)

    def test_id_is_returned_without_an_api_call(self):
        self.assertEqual(kbh.resolve_knowledge_base_id("58ZLOFOJXN"), "58ZLOFOJXN")
        self.assertEqual(self.client.calls, [])

    def test_exact_name_of_a_kb_outside_the_stack_resolves(self):
        self.assertEqual(kbh.resolve_knowledge_base_id("partner-media-kb"), "58ZLOFOJXN")

    def test_short_stack_name_resolves_with_prefix_and_unique_id(self):
        self.assertEqual(kbh.resolve_knowledge_base_id("Advertising"), "2WG26EVR2Z")

    def test_all_pages_are_listed(self):
        kbh.resolve_knowledge_base_id("partner-media-kb")
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.client.calls[1].get("nextToken"), "page-2")

    def test_name_map_is_cached_between_lookups(self):
        kbh.resolve_knowledge_base_id("Advertising")
        kbh.resolve_knowledge_base_id("partner-media-kb")
        self.assertEqual(len(self.client.calls), 2)

    def test_unknown_name_returns_none(self):
        self.assertIsNone(kbh.resolve_knowledge_base_id("no-such-kb"))

    def test_empty_value_returns_none(self):
        self.assertIsNone(kbh.resolve_knowledge_base_id(""))
        self.assertIsNone(kbh.resolve_knowledge_base_id(None))


if __name__ == "__main__":
    unittest.main()
