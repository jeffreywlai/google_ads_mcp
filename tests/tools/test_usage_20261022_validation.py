"""Client-visible regressions for the report's older open validation items."""

from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
import pytest

from ads_mcp import server
from ads_mcp.tools import api
from ads_mcp.tools import audiences
from ads_mcp.tools import changes
from ads_mcp.tools import docs


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", ["direct", "proxy"])
@pytest.mark.parametrize("resource_type", ["SHARED_SET", "SHARED_CRITERION"])
async def test_status_only_change_types_get_actionable_tool_error(
    dispatch, resource_type
):
  arguments = {
      "customer_id": "123",
      "change_resource_types": [resource_type],
      "lookback_days": 7,
  }
  with mock.patch.object(changes, "_account_today") as calendar:
    async with Client(server.mcp_server) as client:
      with pytest.raises(ToolError) as error:
        if dispatch == "direct":
          await client.call_tool("list_change_events", arguments)
        else:
          await client.call_tool(
              "call_tool",
              {"name": "list_change_events", "arguments": arguments},
          )
  calendar.assert_not_called()
  message = str(error.value)
  assert "Unsupported change_resource_types" in message
  assert resource_type in message
  if resource_type == "SHARED_SET":
    assert "get_change_history_extended" in message
  else:
    assert "Use one of" in message
  assert "validation error" not in message


@pytest.mark.parametrize(
    "tool", ["search_google_ads_fields", "export_google_ads_fields_csv"]
)
def test_field_search_or_is_rejected_before_network(tool):
  query = (
      "SELECT name WHERE name LIKE 'campaign.%' "
      "OR name LIKE 'asset_group.%'"
  )
  with mock.patch.object(docs, "get_ads_client") as client:
    with pytest.raises(ToolError, match="not OR"):
      getattr(docs, tool)(query)
  client.assert_not_called()


def test_field_search_or_in_quoted_text_is_not_an_operator():
  query = "SELECT name WHERE name LIKE '%OR%'"
  with mock.patch.object(
      docs,
      "_field_search_page_response",
      return_value={"fields": []},
  ) as search:
    assert docs.search_google_ads_fields(query) == {"fields": []}
  search.assert_called_once_with(query, 50, None)


@pytest.mark.parametrize("tool", ["execute_gaql", "export_gaql_csv"])
@pytest.mark.parametrize("resource_type", ["SHARED_SET", "CAMPAIGN"])
def test_open_change_event_window_fails_locally_with_recovery(
    tool, resource_type
):
  query = (
      "SELECT change_event.resource_name FROM change_event "
      f"WHERE change_event.change_resource_type = '{resource_type}' "
      "AND change_event.change_date_time >= '2026-10-01' LIMIT 100"
  )
  with mock.patch.object(api, "get_ads_client") as client:
    with pytest.raises(ToolError, match="list_change_events"):
      getattr(api, tool)(query, "123")
  client.assert_not_called()


def test_empty_interest_search_gives_broader_search_guidance_without_retry():
  with mock.patch.object(
      audiences,
      "run_gaql_query_page",
      return_value={
          "rows": [],
          "total_results_count": 0,
          "next_page_token": None,
      },
  ) as read:
    result = audiences.search_user_interests(
        "123", query="shirt", taxonomy_types=["IN_MARKET"]
    )
  assert result["returned_count"] == 0
  assert "broader" in result["search_hint"]
  assert "taxonomy_types" in result["search_hint"]
  read.assert_called_once()
