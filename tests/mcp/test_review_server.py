"""Tests for the standalone lifecycle review MCP server."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastmcp import Client

from basic_memory.lifecycle import FIELD_WEIGHT
from basic_memory.mcp import review_server
from basic_memory.mcp.review_server import build_review_server

REVIEW_TOOL_NAMES = {
    "bm_memory_review_propose",
    "bm_memory_review_list",
    "bm_memory_review_decide",
    "bm_memory_review_apply",
    "bm_memory_review_status",
}


def _workspace_tmp(name: str) -> Path:
    p = Path(__file__).resolve().parents[2] / ".lifecycle-test-tmp" / name
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def queue_env(monkeypatch):
    """Point the review queue at a workspace-local file and enable lifecycle."""
    monkeypatch.setenv("BASIC_MEMORY_LIFECYCLE", "1")
    monkeypatch.setenv("BASIC_MEMORY_REVIEW_QUEUE", str(_workspace_tmp("review-server") / "queue.json"))


def test_review_server_documents_bm_edit_command(queue_env):
    """The standalone server instructions must use the 1.0 tool parameter name."""
    server = build_review_server()
    assert server.instructions is not None
    assert "bm_edit(command='set_metadata')" in server.instructions
    assert "bm_edit(operation=" not in review_server.__doc__


@pytest.mark.asyncio
async def test_review_server_exposes_only_review_tools(queue_env):
    server = build_review_server()
    async with Client(server) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == REVIEW_TOOL_NAMES


@pytest.mark.asyncio
async def test_review_flow_end_to_end(queue_env):
    server = build_review_server()
    async with Client(server) as client:
        # propose
        result = await client.call_tool(
            "bm_memory_review_propose",
            {"proposals": [{
                "operation": "update",
                "permalink": "memory://lin",
                "metadata": {FIELD_WEIGHT: 2.0},
                "reason": "scene change",
            }], "output_format": "json"},
        )
        assert result.structured_content["result"]["queued"] == 1
        proposal_id = result.structured_content["result"]["ids"][0]

        # list
        result = await client.call_tool("bm_memory_review_list", {"output_format": "json"})
        assert len(result.structured_content["result"]["pending"]) == 1

        # decide
        result = await client.call_tool(
            "bm_memory_review_decide",
            {"proposal_id": proposal_id, "approve": True, "output_format": "json"},
        )
        assert result.structured_content["result"]["proposal"]["status"] == "approved"

        # apply
        result = await client.call_tool(
            "bm_memory_review_apply", {"proposal_id": proposal_id, "output_format": "json"}
        )
        assert result.structured_content["result"]["metadata"] == {FIELD_WEIGHT: 2.0}
        assert "bm_edit" in result.structured_content["result"]["next_call"]

        # status: the apply tool only returns the patch, so the proposal
        # stays approved until a writer applies and marks it applied
        result = await client.call_tool("bm_memory_review_status", {"output_format": "json"})
        counts = result.structured_content["result"]["counts"]
        assert counts.get("approved") == 1
        assert counts.get("pending", 0) == 0


@pytest.mark.asyncio
async def test_review_tools_refuse_when_disabled(monkeypatch):
    monkeypatch.delenv("BASIC_MEMORY_LIFECYCLE", raising=False)
    server = build_review_server()
    async with Client(server) as client:
        result = await client.call_tool("bm_memory_review_list", {})
        assert result.structured_content["result"]["code"] == "LIFECYCLE_DISABLED"


@pytest.mark.asyncio
async def test_review_apply_refuses_unapproved(queue_env):
    server = build_review_server()
    async with Client(server) as client:
        result = await client.call_tool(
            "bm_memory_review_propose",
            {"proposals": [{"operation": "archive", "permalink": "memory://x", "reason": "stale"}],
             "output_format": "json"},
        )
        proposal_id = result.structured_content["result"]["ids"][0]
        result = await client.call_tool(
            "bm_memory_review_apply", {"proposal_id": proposal_id, "output_format": "json"}
        )
        assert result.structured_content["result"]["ok"] is False
        assert "not approved" in result.structured_content["result"]["message"]


def test_main_fails_fast_when_lifecycle_disabled(monkeypatch):
    from basic_memory.mcp.review_server import main

    monkeypatch.delenv("BASIC_MEMORY_LIFECYCLE", raising=False)
    with pytest.raises(SystemExit) as exc:
        main()
    assert "BASIC_MEMORY_LIFECYCLE" in str(exc.value)
