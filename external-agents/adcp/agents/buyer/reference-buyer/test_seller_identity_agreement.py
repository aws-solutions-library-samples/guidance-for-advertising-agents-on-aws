"""The buyer's `agent_url` for a seller must equal that seller's own `THIS_SELLER_URL`.

This is the guard for a defect that produced a total governed-buy failure with every unit test green.

The chain: the buyer sends `agent_url` as `check_governance`'s `target_agent` -> the governance agent
signs it into the token's `aud` -> the seller's `verify_intent_token` compares `aud` against its own
`THIS_SELLER_URL`. Three components, one value, and nothing asserted they agreed.

They did not. No `SELLER_AGENTS_JSON` entry declared an `agent_url`, so `seller_agents.py` fell back to
`url` -- the AgentCore *invocation* endpoint:

    buyer sent    https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/<arn>/invocations?...
    seller wanted https://reference-seller.example/adcp/mcp

Every governed `create_media_buy` came back `PERMISSION_DENIED: "governance verification failed"`,
immediately after the governance agent returned `approved` for the same action. Each half was
internally consistent; the pair was wrong.

`url` (where to call) and `agent_url` (who the seller is) are different things. This test is what stops
them being conflated again, and it reads BOTH sides from their real sources rather than restating either.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

from seller_agents import resolve_seller_agent  # noqa: E402

SELLERS_ROOT = Path(__file__).resolve().parents[2] / "seller"

#: Sellers this project deploys, and where each keeps its own canonical identity. A third-party
#: seller is excluded deliberately: its invoke URL IS its identity, and this repo does not own its
#: source to read a constant from.
DEPLOYED_SELLERS = {
    "reference": SELLERS_ROOT / "reference-seller" / "app" / "adcpRefSeller",
    "poseidon": SELLERS_ROOT / "poseidon-seller" / "app" / "adcpPoseidonSeller",
    "gotham": SELLERS_ROOT / "gotham-seller" / "app" / "adcpGothamSeller",
}


def _assigned(source: ast.Module, name: str) -> ast.expr | None:
    for node in source.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
    return None


def seller_declared_identity(app_dir: Path) -> str | None:
    """The seller's own `THIS_SELLER_URL`, parsed from its `governance_verification.py`.

    Parsed rather than imported: importing would need that seller's venv and would run its module-level
    configuration checks. Resolves one level of aliasing, which is how reference-seller declares it
    (`from fixtures import DEFAULT_AGENT_URL`).
    """
    module = app_dir / "governance_verification.py"
    if not module.exists():
        return None
    value = _assigned(ast.parse(module.read_text(encoding="utf-8")), "THIS_SELLER_URL")
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    if isinstance(value, ast.Name):
        for sibling in sorted(app_dir.glob("*.py")):
            alias = _assigned(ast.parse(sibling.read_text(encoding="utf-8")), value.id)
            if isinstance(alias, ast.Constant) and isinstance(alias.value, str):
                return alias.value
    return None


@pytest.mark.parametrize("seller_id", sorted(DEPLOYED_SELLERS))
class TestBuyerAndSellerAgreeOnSellerIdentity:
    def test_the_seller_declares_a_canonical_identity(self, seller_id):
        assert seller_declared_identity(DEPLOYED_SELLERS[seller_id]) is not None, (
            f"{seller_id} declares no THIS_SELLER_URL in governance_verification.py, so there is "
            "nothing for the buyer's agent_url to agree with."
        )

    def test_the_registry_agent_url_matches_what_the_seller_verifies_against(self, seller_id):
        expected = seller_declared_identity(DEPLOYED_SELLERS[seller_id])
        resolved = resolve_seller_agent(seller_id)
        assert resolved["agent_url"] == expected, (
            f"The buyer would send {resolved['agent_url']!r} as check_governance's target_agent, but "
            f"{seller_id} verifies an incoming token's `aud` against {expected!r}. Every governed buy "
            "with this seller would be rejected PERMISSION_DENIED. Fix SELLER_AGENTS_JSON's agent_url "
            "(deploy_all.py step 9 writes it from the seller's own constant)."
        )

    def test_agent_url_is_not_the_invoke_endpoint(self, seller_id):
        """The specific fallback that caused the outage. `url` is where to call; `agent_url` is who."""
        resolved = resolve_seller_agent(seller_id)
        assert "/invocations" not in resolved["agent_url"], (
            f"{seller_id}'s agent_url is an AgentCore invoke endpoint, which means no agent_url was "
            "declared and seller_agents.py fell back to `url`."
        )
        assert resolved["agent_url"] != resolved["url"]
