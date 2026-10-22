"""Offline access and workflow regressions for locked mutation discovery."""

import asyncio
from unittest import mock

from fastmcp import Client
from fastmcp import FastMCP
from fastmcp.server.context import Context
import pytest

# Import the public registry so focused runs have the complete real catalog.
from ads_mcp import registry  # pylint: disable=unused-import
from ads_mcp.coordinator import mcp_server
from ads_mcp.coordinator import NonMutationVisibleSearchTransform
from ads_mcp.routing import resolve_intent
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import compact_search_result_serializer
from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import session_control_tool
from ads_mcp.tools import docs


def _isolated_server():
  """Builds a credential-free server using the actual visibility transform."""
  server = FastMCP("isolated-discovery")
  called = []

  @ads_mutation_tool(server)
  def allowed_write():
    """Performs the public quasar write."""
    called.append("allowed")
    return {"called": True}

  @ads_mutation_tool(server, auth=lambda _ctx: False)
  def denied_write():
    """Performs the unauthorized quasar write."""
    called.append("denied")
    return {"called": True}

  @ads_mutation_tool(server)
  def named_write():
    """Performs the named private quasar write."""
    called.append("named")
    return {"called": True}

  @ads_mutation_tool(server, tags={"private"})
  def tagged_write():
    """Performs the tagged private quasar write."""
    called.append("tagged")
    return {"called": True}

  @ads_mutation_tool(server)
  def keyed_write():
    """Performs the keyed private quasar write."""
    called.append("keyed")
    return {"called": True}

  @ads_mutation_tool(
      server,
      meta={"fastmcp": {"app": "review"}, "ui": {"visibility": ["app"]}},
  )
  def backend_write():
    """Performs the backend private quasar write."""
    called.append("backend")
    return {"called": True}

  @session_control_tool(server)
  async def unlock(ctx: Context):
    """Unlocks mutation calls for the current test client."""
    await ctx.enable_components(tags={MUTATE_TAG}, components={"tool"})
    return {"unlocked": True}

  @session_control_tool(server)
  async def lock(ctx: Context):
    """Locks mutation calls for the current test client."""
    await ctx.disable_components(tags={MUTATE_TAG}, components={"tool"})
    return {"unlocked": False}

  @session_control_tool(server)
  async def disable_named(ctx: Context):
    """Hides one mutation only for the current test client."""
    await ctx.disable_components(
        names={"allowed_write"}, tags={MUTATE_TAG}, components={"tool"}
    )
    return {"disabled": True}

  @session_control_tool(server)
  async def profile(ctx: Context):
    """Reports the current client's actual mutation visibility profile."""
    return await docs.get_tool_visibility_profile(ctx)

  server.disable(tags={MUTATE_TAG}, components={"tool"})
  server.add_transform(
      NonMutationVisibleSearchTransform(
          search_result_serializer=compact_search_result_serializer
      )
  )
  return server, called


async def _quasar_results(client):
  response = await client.call_tool("search_tools", {"query": "quasar"})
  assert not response.is_error
  return {item["name"]: item for item in response.structured_content["result"]}


def test_locked_search_retains_auth_named_tag_key_and_backend_visibility():
  async def check():
    server, called = _isolated_server()
    server.disable(names={"named_write"}, components={"tool"})
    server.disable(tags={"private"}, components={"tool"})
    server.disable(keys={"tool:keyed_write@"}, components={"tool"})
    async with Client(server) as client:
      before = {tool.name for tool in await client.list_tools()}
      results = await _quasar_results(client)
      after = {tool.name for tool in await client.list_tools()}
      assert results.keys() == {"allowed_write"}
      assert results["allowed_write"]["locked"] is True
      assert (
          "unlock_mutation_tools"
          in results["allowed_write"]["required_action"]
      )
      assert before == after
      assert "allowed_write" not in after
    assert not called

  asyncio.run(check())


def test_named_session_disable_is_preserved_without_changing_other_clients():
  async def check():
    server, called = _isolated_server()
    async with Client(server) as first, Client(server) as second:
      await first.call_tool("disable_named", {})
      first_results = await _quasar_results(first)
      second_results = await _quasar_results(second)
      assert "allowed_write" not in first_results
      assert second_results["allowed_write"]["locked"] is True
      assert "denied_write" not in first_results
      assert "denied_write" not in second_results
    assert not called

  asyncio.run(check())


def test_search_descriptions_never_unlock_direct_or_proxy_mutation_calls():
  async def check():
    server, called = _isolated_server()
    async with Client(server) as first, Client(server) as second:
      assert (await _quasar_results(first))["allowed_write"]["locked"] is True
      direct = await first.call_tool("allowed_write", {}, raise_on_error=False)
      proxy = await first.call_tool(
          "call_tool",
          {"name": "allowed_write", "arguments": {}},
          raise_on_error=False,
      )
      assert direct.is_error
      assert proxy.is_error
      assert not called
      await first.call_tool("unlock", {})
      active = (await _quasar_results(first))["allowed_write"]
      assert active["locked"] is False
      assert "required_action" not in active
      assert (await _quasar_results(second))["allowed_write"]["locked"] is True
      active_call = await first.call_tool("allowed_write", {})
      assert not active_call.is_error
      await first.call_tool("lock", {})
      assert (await _quasar_results(first))["allowed_write"]["locked"] is True
      relocked = await first.call_tool(
          "allowed_write", {}, raise_on_error=False
      )
      assert relocked.is_error
    assert called == ["allowed"]

  asyncio.run(check())


def test_scoped_session_disable_does_not_relock_other_mutations():
  async def check():
    server, called = _isolated_server()
    async with Client(server) as client:
      initial_profile = await client.call_tool("profile", {})
      assert initial_profile.data["mutation_tools_unlocked"] is False
      await client.call_tool("unlock", {})
      await client.call_tool("disable_named", {})
      listed = {tool.name for tool in await client.list_tools()}
      assert "allowed_write" not in listed
      assert "named_write" in listed
      results = await _quasar_results(client)
      assert "allowed_write" not in results
      assert results["named_write"]["locked"] is False
      scoped_profile = await client.call_tool("profile", {})
      assert scoped_profile.data["mutation_tools_unlocked"] is True
      await client.call_tool("lock", {})
      relocked_profile = await client.call_tool("profile", {})
      assert relocked_profile.data["mutation_tools_unlocked"] is False
    assert not called

  asyncio.run(check())


@pytest.mark.parametrize(
    "query",
    [
        "copy asset group to another campaign",
        "copy the asset group with the highest ROAS to another campaign",
        "set campaign target ROAS",
        "update campaign geo_target_type_setting",
        "update campaign geo targeting setting",
        "update campaign geo targeting settings",
        "create a YouTube video asset",
        "create a YouTube video asset from the processed upload",
        "create a Demand Gen video ad",
    ],
)
def test_real_write_workflows_expose_locked_native_mutation_and_schema(query):
  async def check():
    async with Client(mcp_server) as client:
      response = await client.call_tool("search_tools", {"query": query})
      results = response.structured_content["result"]
      assert [item["name"] for item in results[:2]] == [
          "mutate_ads_resources",
          "get_resource_mutation_schema",
      ]
      assert results[0]["locked"] is True
      assert "unlock_mutation_tools" in results[0]["required_action"]
      assert results[1]["mode"] == "read"
      assert "locked" not in results[1]
      listed = {tool.name for tool in await client.list_tools()}
      assert "mutate_ads_resources" not in listed
      await client.call_tool("unlock_mutation_tools", {})
      active = await client.call_tool("search_tools", {"query": query})
      assert active.structured_content["result"][0]["locked"] is False

  asyncio.run(check())


@pytest.mark.parametrize(
    "query",
    [
        "do not upload a video",
        "do not copy the asset group with the highest ROAS",
        "how do I create a YouTube video asset",
        "show Demand Gen video ads",
        "get video upload processing status",
        "poll YouTube video processing",
        "video upload details",
        "do not upload a video and wait for processing",
        "how do I upload a video and wait for processing",
        "when did we upload the YouTube video",
        "show campaign geo targeting settings",
        "show asset group performance",
    ],
)
def test_read_advisory_and_negated_workflows_exclude_remote_mutations(query):
  async def check():
    async with Client(mcp_server) as client:
      for unlocked in (False, True):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
        response = await client.call_tool("search_tools", {"query": query})
        assert all(
            item["mode"] != "mutate"
            for item in response.structured_content["result"]
        )

  asyncio.run(check())


@pytest.mark.parametrize(
    "query,expected,read_only",
    [
        (
            "upload a video and wait for processing",
            "upload_youtube_video",
            False,
        ),
        (
            "upload an mp4 and poll until processed",
            "upload_youtube_video",
            False,
        ),
        ("poll YouTube video processing", "get_youtube_video_upload", True),
        ("video upload details", "get_youtube_video_upload", True),
        ("YouTube video processing state", "get_youtube_video_upload", True),
        (
            "get details of YouTube video uploads",
            "get_youtube_video_upload",
            True,
        ),
        (
            "update campaign geo targeting setting",
            "mutate_ads_resources",
            False,
        ),
        (
            "create a YouTube video asset from the processed upload",
            "mutate_ads_resources",
            False,
        ),
    ],
)
def test_video_and_geo_intents_preserve_the_requested_effect(
    query, expected, read_only
):
  decision = resolve_intent(query)
  assert decision.target == expected
  assert decision.exclude_remote_mutations is read_only
  assert decision.requires_mutation_visibility is not read_only


@pytest.mark.parametrize("dispatch", ["direct", "proxy"])
def test_upload_and_wait_discovery_keeps_execution_locked(dispatch):
  async def check():
    async with Client(mcp_server) as client:
      response = await client.call_tool(
          "search_tools", {"query": "upload a video and wait for processing"}
      )
      upload = response.structured_content["result"][0]
      assert upload["name"] == "upload_youtube_video"
      assert upload["locked"] is True
      assert "unlock_mutation_tools" in upload["required_action"]
      arguments = {
          "customer_id": "123",
          "file_path": "/unused.mp4",
          "title": "Example",
      }
      name = upload["name"]
      if dispatch == "proxy":
        arguments = {"name": name, "arguments": arguments}
        name = "call_tool"
      result = await client.call_tool(name, arguments, raise_on_error=False)
      assert result.is_error
      error = " ".join(item.text for item in result.content).lower()
      assert "unknown tool" in error or "not found" in error

  asyncio.run(check())


@pytest.mark.parametrize(
    "scope",
    [
        {"names": ["one_mutation"]},
        {"keys": ["tool:one_mutation@"]},
        {"version": {"eq": "1"}},
        {"match_all": True},
    ],
)
def test_visibility_profile_ignores_scoped_mutation_rules(scope):
  rules = [
      {"enabled": True, "tags": ["mutate"], "components": ["tool"]},
      {
          "enabled": False,
          "tags": ["mutate"],
          "components": ["tool"],
          **scope,
      },
  ]
  with mock.patch.object(
      docs, "get_visibility_rules", new_callable=mock.AsyncMock
  ) as read_rules:
    read_rules.return_value = rules
    result = asyncio.run(docs.get_tool_visibility_profile(mock.Mock()))
  assert result["mutation_tools_unlocked"] is True
  assert result["session_rules"] == rules
