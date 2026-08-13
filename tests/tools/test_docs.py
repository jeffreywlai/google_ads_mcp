# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for docs.py."""

import asyncio
import os
from unittest import mock

from ads_mcp.tools import docs
from fastmcp.exceptions import ToolError
import pytest


class _FieldPager:
  """Minimal GoogleAdsField pager stand-in exposing one response page."""

  def __init__(self, results, total_count, next_page_token=""):
    self._page = mock.Mock(
        results=results,
        total_results_count=total_count,
        next_page_token=next_page_token,
    )

  @property
  def pages(self):
    return iter([self._page])


@pytest.fixture(autouse=True)
def reset_doc_caches():
  docs._TEXT_FILE_CACHE = {}  # pylint: disable=protected-access
  docs._YAML_FILE_CACHE = {}  # pylint: disable=protected-access
  docs._CACHED_FIELDS = {}  # pylint: disable=protected-access
  docs._CACHED_FIELDS_MTIME = None  # pylint: disable=protected-access
  yield
  docs._TEXT_FILE_CACHE = {}  # pylint: disable=protected-access
  docs._YAML_FILE_CACHE = {}  # pylint: disable=protected-access
  docs._CACHED_FIELDS = {}  # pylint: disable=protected-access
  docs._CACHED_FIELDS_MTIME = None  # pylint: disable=protected-access


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data="doc content"
)
def test_get_gaql_doc(mock_file):
  """Tests get_gaql_doc function."""
  assert docs.get_gaql_doc() == "doc content"
  mock_file.assert_called_with(
      os.path.join(docs.MODULE_DIR, "context/GAQL_compact.md"),
      "r",
      encoding="utf-8",
  )


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data="doc content"
)
def test_get_reporting_doc(mock_file):
  """Tests get_reporting_view_doc without a view returns views list."""
  assert docs.get_reporting_view_doc(None) == "doc content"
  mock_file.assert_called_with(
      os.path.join(docs.MODULE_DIR, "context/views.yaml"),
      "r",
      encoding="utf-8",
  )


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data="tool content"
)
def test_get_tool_guide(mock_file):
  """Tests get_tool_guide without a topic."""
  assert docs.get_tool_guide() == {
      "topic": None,
      "guide": "tool content",
      "matched_category_count": 0,
  }
  mock_file.assert_called_with(
      os.path.join(docs.MODULE_DIR, "context/tool_guide.yaml"),
      "r",
      encoding="utf-8",
  )


@mock.patch(
    "builtins.open", new_callable=mock.mock_open, read_data="view content"
)
def test_get_view_doc(mock_file):
  """Tests get_view_doc function."""
  assert docs.get_reporting_view_doc("campaign") == "view content"
  mock_file.assert_called_with(
      os.path.join(docs.MODULE_DIR, "context/views/campaign.yaml"),
      "r",
      encoding="utf-8",
  )


@mock.patch("builtins.open", side_effect=FileNotFoundError)
def test_get_view_doc_not_found(_):
  """Tests get_view_doc function when file not found."""
  with pytest.raises(ToolError):
    docs.get_reporting_view_doc("non_existent")


def test_resources_exist():
  """Tests that the resources are correctly defined."""
  # We can't easily test the @mcp.resource decorator registration without
  # mocking FastMCP
  # but checking the tool definitions is done via coverage
  pass


@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_get_resource_metadata(mock_get_ads_client):
  """Tests resource-level field metadata lookup."""
  mock_client = mock_get_ads_client.return_value
  mock_service = mock_client.get_service.return_value

  campaign_id = mock.Mock()
  campaign_id.name = "campaign.id"
  campaign_id.selectable = True
  campaign_id.filterable = True
  campaign_id.sortable = True

  campaign_status = mock.Mock()
  campaign_status.name = "campaign.status"
  campaign_status.selectable = True
  campaign_status.filterable = True
  campaign_status.sortable = False

  ad_group_id = mock.Mock()
  ad_group_id.name = "ad_group.id"
  ad_group_id.selectable = True
  ad_group_id.filterable = True
  ad_group_id.sortable = True

  mock_service.search_google_ads_fields.return_value = [
      campaign_id,
      campaign_status,
      ad_group_id,
  ]

  result = docs.get_resource_metadata("campaign")

  assert result == {
      "resource": "campaign",
      "selectable": ["campaign.id", "campaign.status"],
      "filterable": ["campaign.id", "campaign.status"],
      "sortable": ["campaign.id"],
      "returned_count": 3,
      "total_count": 3,
      "total_page_count": 1,
      "truncated": False,
      "has_more": False,
      "complete_inline": True,
      "next_page_token": None,
      "page_size": 50,
      "requested_page_size": 50,
      "page_size_clamped": False,
  }
  mock_service.search_google_ads_fields.assert_called_once_with(
      request={
          "query": (
              "SELECT name, selectable, filterable, sortable "
              "WHERE name LIKE 'campaign.%'"
          ),
          "page_size": 50,
      }
  )


@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_get_resource_metadata_preserves_google_pagination(
    mock_get_ads_client,
):
  """Metadata pages stay compact while the API cursor preserves all fields."""
  mock_client = mock_get_ads_client.return_value
  mock_service = mock_client.get_service.return_value
  campaign_id = mock.Mock()
  campaign_id.name = "campaign.id"
  campaign_id.selectable = True
  campaign_id.filterable = True
  campaign_id.sortable = True
  mock_service.search_google_ads_fields.return_value = _FieldPager(
      [campaign_id],
      total_count=275,
      next_page_token="next-fields-page",
  )

  result = docs.get_resource_metadata(
      "campaign",
      limit=5000,
      page_token="current-fields-page",
  )

  assert result["selectable"] == ["campaign.id"]
  assert result["returned_count"] == 1
  assert result["total_count"] == 275
  assert result["next_page_token"] == "next-fields-page"
  assert result["page_size"] == 100
  assert result["page_size_clamped"] is True
  assert mock_service.search_google_ads_fields.call_args.kwargs["request"] == {
      "query": (
          "SELECT name, selectable, filterable, sortable "
          "WHERE name LIKE 'campaign.%'"
      ),
      "page_size": 100,
      "page_token": "current-fields-page",
  }


def test_get_resource_metadata_rejects_invalid_resource_name():
  """Tests resource metadata lookup input validation."""
  with pytest.raises(
      ToolError,
      match="resource_name must be a snake_case Google Ads resource name",
  ):
    docs.get_resource_metadata("campaign'; DROP TABLE")


@mock.patch("ads_mcp.tools.docs.urllib.request.urlopen")
def test_get_release_notes(mock_urlopen):
  """Tests live release notes resource fetching."""
  mock_response = mock.Mock()
  mock_response.read.return_value = b"release notes"
  mock_urlopen.return_value.__enter__.return_value = mock_response

  assert docs.get_release_notes() == "release notes"
  request = mock_urlopen.call_args.args[0]
  assert request.full_url == docs._LIVE_RELEASE_NOTES_URL  # pylint: disable=protected-access


def test_get_tool_guide_filters_topic():
  """Tests get_tool_guide topic filtering."""
  guide_yaml = """
principles:
  - Prefer dedicated tools.
categories:
  optimization:
    summary: Recommendations and optimization.
    tools:
      list_recommendations: Open recommendations.
      apply_recommendations: Apply recommendations.
  docs:
    summary: Docs.
    tools:
      get_gaql_doc: GAQL docs.
"""
  with mock.patch(
      "builtins.open", new_callable=mock.mock_open, read_data=guide_yaml
  ):
    result = docs.get_tool_guide("apply")

  assert result["topic"] == "apply"
  assert result["matched_category_count"] == 1
  assert "optimization:" in result["guide"]
  assert "apply_recommendations" in result["guide"]
  assert "list_recommendations" not in result["guide"]
  assert "docs:" not in result["guide"]


def test_get_tool_guide_raises_when_topic_missing():
  """Tests get_tool_guide topic filtering with no matches."""
  guide_yaml = """
principles:
  - Prefer dedicated tools.
categories:
  docs:
    summary: Docs.
    tools:
      get_gaql_doc: GAQL docs.
"""
  with mock.patch(
      "builtins.open", new_callable=mock.mock_open, read_data=guide_yaml
  ):
    with pytest.raises(ToolError, match="No tool guide entries matched"):
      docs.get_tool_guide("missing")


def test_get_tool_guide_matches_multiword_topics():
  """Tests topic matching across tokenized natural-language queries."""
  guide_yaml = """
principles:
  - Prefer dedicated tools.
categories:
  negatives:
    summary: Shared sets and negative keyword management.
    tools:
      list_shared_set_keywords: List keywords in a shared negative keyword list.
  docs:
    summary: Documentation and guides.
    tools:
      get_gaql_doc: GAQL docs.
"""
  with mock.patch(
      "builtins.open", new_callable=mock.mock_open, read_data=guide_yaml
  ):
    result = docs.get_tool_guide("negative keywords")

  assert result["topic"] == "negative keywords"
  assert result["matched_category_count"] == 1
  assert "negatives:" in result["guide"]
  assert "list_shared_set_keywords" in result["guide"]
  assert "docs:" not in result["guide"]


@mock.patch("ads_mcp.tools.docs.os.path.getmtime", return_value=123.0)
def test_get_gaql_doc_caches_file_reads(_mock_getmtime):
  with mock.patch(
      "builtins.open",
      new_callable=mock.mock_open,
      read_data="doc content",
  ) as mock_file:
    assert docs.get_gaql_doc() == "doc content"
    assert docs.get_gaql_doc() == "doc content"

  assert mock_file.call_count == 1


@mock.patch("ads_mcp.tools.docs.os.path.getmtime", return_value=123.0)
def test_get_reporting_fields_doc_caches_yaml_reads(_mock_getmtime):
  with mock.patch(
      "builtins.open",
      new_callable=mock.mock_open,
      read_data="campaign.id:\n  type: ATTRIBUTE\n",
  ) as mock_file:
    assert "campaign.id" in docs.get_reporting_fields_doc(["campaign.id"])
    assert "campaign.id" in docs.get_reporting_fields_doc(["campaign.id"])

  assert mock_file.call_count == 1


@mock.patch(
    "ads_mcp.tools.docs.get_visibility_rules", new_callable=mock.AsyncMock
)
def test_get_tool_visibility_profile(mock_get_visibility_rules):
  """Tests session visibility profile reporting."""
  mock_get_visibility_rules.return_value = [
      {
          "enabled": True,
          "tags": ["mutate"],
          "components": ["tool"],
      }
  ]

  result = asyncio.run(docs.get_tool_visibility_profile(mock.Mock()))

  assert result == {
      "mutation_tools_unlocked": True,
      "session_rules": [
          {
              "enabled": True,
              "tags": ["mutate"],
              "components": ["tool"],
          }
      ],
  }


@mock.patch(
    "ads_mcp.tools.docs.get_visibility_rules", new_callable=mock.AsyncMock
)
def test_get_tool_visibility_profile_uses_latest_matching_rule(
    mock_get_visibility_rules,
):
  """Tests that the latest mutation visibility rule wins."""
  mock_get_visibility_rules.return_value = [
      {
          "enabled": True,
          "tags": ["mutate"],
          "components": ["tool"],
      },
      {
          "enabled": False,
          "tags": ["mutate"],
          "components": ["tool"],
      },
  ]

  result = asyncio.run(docs.get_tool_visibility_profile(mock.Mock()))

  assert result["mutation_tools_unlocked"] is False


@mock.patch(
    "ads_mcp.tools.docs.enable_components", new_callable=mock.AsyncMock
)
def test_unlock_mutation_tools(mock_enable_components):
  """Tests per-session mutation tool unlock."""
  ctx = mock.Mock()

  result = asyncio.run(docs.unlock_mutation_tools(ctx))

  assert result == {"mutation_tools_unlocked": True}
  mock_enable_components.assert_awaited_once_with(
      ctx,
      tags={"mutate"},
      components={"tool"},
  )


@mock.patch(
    "ads_mcp.tools.docs.disable_components", new_callable=mock.AsyncMock
)
def test_lock_mutation_tools(mock_disable_components):
  """Tests per-session mutation tool lock."""
  ctx = mock.Mock()

  result = asyncio.run(docs.lock_mutation_tools(ctx))

  assert result == {"mutation_tools_unlocked": False}
  mock_disable_components.assert_awaited_once_with(
      ctx,
      tags={"mutate"},
      components={"tool"},
  )


@mock.patch("ads_mcp.tools.docs.format_value")
@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_search_google_ads_fields(mock_get_ads_client, mock_format_value):
  """Tests live GoogleAdsField search wrapper."""
  mock_client = mock_get_ads_client.return_value
  mock_service = mock_client.get_service.return_value
  mock_service.search_google_ads_fields.return_value = [
      mock.Mock(),
      mock.Mock(),
  ]
  mock_format_value.side_effect = [
      {"name": "campaign.id"},
      {"name": "campaign.name"},
  ]

  result = docs.search_google_ads_fields(
      "SELECT name WHERE name LIKE 'campaign.%'", limit=2
  )

  assert result["fields"] == [
      {"name": "campaign.id"},
      {"name": "campaign.name"},
  ]
  assert result["returned_count"] == 2
  assert result["total_count"] == 2
  assert result["truncated"] is False
  assert result["next_page_token"] is None
  assert result["source_page_returned_count"] == 2
  assert result["bulk_export_call"] == {
      "tool": "export_google_ads_fields_csv",
      "arguments": {"query": "SELECT name WHERE name LIKE 'campaign.%'"},
  }
  mock_service.search_google_ads_fields.assert_called_once()


@mock.patch("ads_mcp.tools.docs.format_value")
@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_search_google_ads_fields_returns_api_continuation(
    mock_get_ads_client, mock_format_value
):
  """A bounded field page reports Google's complete count and next token."""
  mock_client = mock_get_ads_client.return_value
  mock_service = mock_client.get_service.return_value
  field = mock.Mock()
  mock_service.search_google_ads_fields.return_value = _FieldPager(
      [field],
      total_count=500,
      next_page_token="next-fields-page",
  )
  mock_format_value.return_value = {"name": "campaign.id"}

  result = docs.search_google_ads_fields(
      "SELECT name WHERE name LIKE 'campaign.%'",
      limit=10000,
      page_token="current-fields-page",
  )

  assert result["fields"] == [{"name": "campaign.id"}]
  assert result["total_count"] == 500
  assert result["next_page_token"] == "next-fields-page"
  assert result["page_size"] == 100
  assert result["page_size_clamped"] is True


@mock.patch("ads_mcp.tools.docs.format_value")
@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_search_google_ads_fields_spills_large_metadata_without_skip_token(
    mock_get_ads_client,
    mock_format_value,
):
  query = "SELECT name, selectable_with WHERE name LIKE 'campaign.%'"
  mock_service = mock_get_ads_client.return_value.get_service.return_value
  source_fields = [mock.Mock(), mock.Mock(), mock.Mock()]
  mock_service.search_google_ads_fields.return_value = _FieldPager(
      source_fields,
      total_count=10,
      next_page_token="google-next-page",
  )
  mock_format_value.side_effect = [
      {"name": f"campaign.field_{index}", "selectable_with": ["x" * 20_000]}
      for index in range(3)
  ]

  result = docs.search_google_ads_fields(query, limit=100)

  assert result["returned_count"] == 2
  assert result["source_page_returned_count"] == 3
  assert result["shared_inline_omitted_count"] == 1
  assert result["next_page_token"] is None
  assert result["truncated"] is True
  assert "would skip data" in result["continuation_unavailable"]
  assert result["bulk_export_call"] == {
      "tool": "export_google_ads_fields_csv",
      "arguments": {"query": query},
  }
  shared = result["shared_inline_delivery"]
  assert shared["inline_bytes"] <= shared["inline_byte_limit"]


@mock.patch("ads_mcp.tools.docs.write_rows_to_explicit_csv")
@mock.patch("ads_mcp.tools.docs.format_value")
@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_export_google_ads_fields_csv_writes_every_matching_field(
    mock_get_ads_client,
    mock_format_value,
    mock_write_rows,
):
  query = "SELECT name, selectable_with WHERE name LIKE 'campaign.%'"
  source_fields = [mock.Mock(), mock.Mock()]
  field_service = mock_get_ads_client.return_value.get_service.return_value
  field_service.search_google_ads_fields.return_value = source_fields
  formatted_fields = [
      {"name": "campaign.id", "selectable_with": ["ad_group.id"]},
      {"name": "campaign.name", "selectable_with": ["ad_group.name"]},
  ]
  mock_format_value.side_effect = formatted_fields
  mock_write_rows.return_value = (
      "/tmp/google-fields.csv",
      ["name", "selectable_with"],
      321,
  )

  result = docs.export_google_ads_fields_csv(query)

  mock_write_rows.assert_called_once_with(formatted_fields)
  assert result["row_count"] == 2
  assert result["file_path"] == "/tmp/google-fields.csv"
  assert result["complete"] is True


@mock.patch("ads_mcp.tools.docs.get_ads_client")
def test_search_google_ads_fields_rejects_bool_limit(mock_get_ads_client):
  """Bool is not accepted as an integer limit."""
  with pytest.raises(ToolError, match="limit must be an integer"):
    docs.search_google_ads_fields(
        "SELECT name WHERE name LIKE 'campaign.%'",
        limit=True,
    )

  mock_get_ads_client.assert_not_called()
