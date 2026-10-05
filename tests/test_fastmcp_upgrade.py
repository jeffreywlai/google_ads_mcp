"""Offline transport and mutation-access regressions for FastMCP upgrades."""

import asyncio
from pathlib import Path
import sys
from unittest import mock

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.providers.addressing import hashed_backend_name
import pytest

from ads_mcp import server
from ads_mcp.tools import campaigns


_MUTATION = "set_campaign_status"
_ARGUMENTS = {
    "customer_id": "123",
    "campaign_id": "456",
    "status": "PAUSED",
}


async def _call_campaign_mutation(client, dispatch):
  if dispatch == "direct":
    return await client.call_tool(_MUTATION, _ARGUMENTS)
  return await client.call_tool(
      "call_tool", {"name": _MUTATION, "arguments": _ARGUMENTS}
  )


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", ["direct", "compatibility"])
async def test_locked_mutation_cannot_be_called_through_either_dispatch(
    dispatch,
):
  """Knowing a mutation name never bypasses a session's visibility lock."""
  ads_client = mock.MagicMock()
  campaign_service = ads_client.get_service.return_value
  resource_name = "customers/123/campaigns/456"
  campaign_service.campaign_path.return_value = resource_name
  campaign_service.mutate_campaigns.return_value.results = [
      mock.Mock(resource_name=resource_name)
  ]

  with mock.patch.object(
      campaigns, "get_ads_client", return_value=ads_client
  ) as get_client:
    async with Client(server.mcp_server) as client:
      for unlocked in (False, True, False):
        if unlocked:
          await client.call_tool("unlock_mutation_tools", {})
          response = await _call_campaign_mutation(client, dispatch)
          assert response.data == {"resource_name": resource_name}
        else:
          await client.call_tool("lock_mutation_tools", {})
          before_calls = get_client.call_count
          with pytest.raises(ToolError, match="Unknown tool"):
            await _call_campaign_mutation(client, dispatch)
          assert get_client.call_count == before_calls
          search = await client.call_tool(
              "search_tools", {"query": "pause campaign 456"}
          )
          assert not search.structured_content["result"]

  get_client.assert_called_once()
  campaign_service.mutate_campaigns.assert_called_once()


@pytest.mark.asyncio
async def test_registered_mutation_does_not_gain_a_hashed_app_alias():
  """Normal Ads tools do not opt into FastMCP's app-only hashed dispatch."""
  alias = hashed_backend_name(server.mcp_server.name, _MUTATION)
  with mock.patch.object(campaigns, "get_ads_client") as get_client:
    async with Client(server.mcp_server) as client:
      for control in ("lock_mutation_tools", "unlock_mutation_tools"):
        await client.call_tool(control, {})
        with pytest.raises(ToolError, match="Unknown tool"):
          await client.call_tool(alias, _ARGUMENTS)
      await client.call_tool("lock_mutation_tools", {})
  get_client.assert_not_called()


@pytest.mark.asyncio
async def test_real_stdio_transport_preserves_discovery_and_mutation_lock():
  """The registered server works over stdio without Ads startup or RPCs."""
  transport = StdioTransport(
      command=sys.executable,
      args=[
          "-c",
          "from ads_mcp.stdio import mcp_server; "
          "mcp_server.run(transport='stdio', show_banner=False)",
      ],
      cwd=str(Path(__file__).resolve().parents[1]),
      keep_alive=False,
  )
  async with asyncio.timeout(15), Client(transport) as client:
    await client.ping()
    names = {tool.name for tool in await client.list_tools()}
    assert {"search_tools", "call_tool", "get_campaign_settings"} <= names
    assert _MUTATION not in names
    with pytest.raises(ToolError, match="Unknown tool"):
      await client.call_tool(_MUTATION, _ARGUMENTS)
    await client.call_tool("unlock_mutation_tools", {})
    assert _MUTATION in {tool.name for tool in await client.list_tools()}
    await client.call_tool("lock_mutation_tools", {})
    assert _MUTATION not in {tool.name for tool in await client.list_tools()}
