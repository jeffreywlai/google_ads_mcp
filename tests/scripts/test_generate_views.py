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

"""Tests for the view generation script."""

import asyncio
import logging
from pathlib import Path
from unittest import mock

from ads_mcp.scripts import generate_views
import httpx
import pytest
import yaml


def _view_json(view: str) -> dict:
  """Builds a minimal valid reporting view response."""
  field_name = f"{view}.id"
  return {
      "display_name": view.replace("_", " ").title(),
      "name": view,
      "description": f"A {view}.",
      "attributes": [field_name],
      "segments": [],
      "metrics": [],
      "fields": {
          field_name: {
              "field_details": {
                  "name": field_name,
                  "description": f"The ID of the {view}.",
                  "category": "ATTRIBUTE",
                  "data_type": "INT64",
                  "is_repeated": False,
                  "enum_values": [],
                  "filterable": True,
                  "sortable": True,
              }
          }
      },
  }


def _write_context(
    context_path: Path,
    declared_views: list[str],
    present_views: set[str] | None = None,
    manifest_views: list[str] | None = None,
) -> None:
  """Writes a minimal reporting context tree for an update test."""
  views_path = context_path / "views"
  views_path.mkdir()
  (context_path / "views.yaml").write_text(
      yaml.safe_dump(declared_views), encoding="utf-8"
  )
  for view in present_views or set():
    (views_path / f"{view}.yaml").write_text(
        "name: existing\n", encoding="utf-8"
    )
  if manifest_views is not None:
    (context_path / ".views-manifest").write_text(
        "\n".join(sorted(manifest_views)), encoding="utf-8"
    )


def test_get_view_json_url():
  """Tests the get_view_json_url function."""
  assert (
      generate_views.get_view_json_url("campaign")
      == "https://gaql-query-builder.uc.r.appspot.com/schemas/v24/campaign.json"
  )


@pytest.mark.asyncio
async def test_get_view_json_checks_status_and_uses_timeout():
  """The endpoint request is bounded and rejects non-success statuses."""
  mock_client = mock.AsyncMock(spec=httpx.AsyncClient)
  mock_response = mock.MagicMock()
  mock_response.json.return_value = {"name": "campaign"}
  mock_client.get.return_value = mock_response

  assert await generate_views.get_view_json("campaign", mock_client) == {
      "name": "campaign"
  }

  mock_client.get.assert_awaited_once_with(
      generate_views.get_view_json_url("campaign"), timeout=15.0
  )
  mock_response.raise_for_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_view_json_propagates_timeout():
  """Endpoint timeouts are returned to the resilient batch updater."""
  mock_client = mock.AsyncMock(spec=httpx.AsyncClient)
  mock_client.get.side_effect = httpx.TimeoutException("request timed out")

  with pytest.raises(httpx.TimeoutException, match="timed out"):
    await generate_views.get_view_json("campaign", mock_client)


@pytest.mark.asyncio
async def test_get_view_json_raises_for_http_error():
  """HTTP errors are returned to the resilient batch updater."""
  request = httpx.Request("GET", "https://example.test/campaign.json")
  response = httpx.Response(404, request=request)
  mock_client = mock.AsyncMock(spec=httpx.AsyncClient)
  mock_client.get.return_value = response

  with pytest.raises(httpx.HTTPStatusError):
    await generate_views.get_view_json("campaign", mock_client)


def test_get_fields_obj():
  """Tests the get_fields_obj function."""
  expected = {
      "campaign.id": {
          "description": "The ID of the campaign.",
          "data_type": "INT64",
          "is_repeated": False,
          "filterable": True,
          "sortable": True,
      }
  }
  assert (
      generate_views.get_fields_obj(_view_json("campaign"), "attributes")
      == expected
  )


def test_get_fields_obj_preserves_segment_metric_compatibility():
  """Generated segment metadata keeps its compatible metric edges."""
  view_json = _view_json("campaign")
  segment_name = "segments.new_versus_returning_customers"
  view_json["segments"] = [segment_name]
  view_json["fields"][segment_name] = {
      "field_details": {
          "name": segment_name,
          "description": "New versus returning customers.",
          "category": "SEGMENT",
          "data_type": "ENUM",
          "is_repeated": False,
          "enum_values": ["NEW", "RETURNING"],
          "filterable": True,
          "sortable": True,
          "selectable_with": [
              "campaign",
              "metrics.conversions_value",
              "segments.week",
              "metrics.conversions",
          ],
      },
      "incompatible_fields": ["metrics.cost_micros"],
  }

  fields = generate_views.get_fields_obj(view_json, "segments")

  assert fields[segment_name]["compatible_metrics"] == [
      "metrics.conversions",
      "metrics.conversions_value",
  ]


def test_check_context_version_requires_compatibility_artifact(tmp_path):
  """A missing compatibility graph forces context regeneration."""
  (tmp_path / "fields.yaml").write_text("{}\n", encoding="utf-8")
  (tmp_path / ".api-version").write_text(
      generate_views.ADS_API_VERSION, encoding="utf-8"
  )
  (tmp_path / ".mcp-server-version").write_text(
      generate_views.MCP_SERVER_VERSION, encoding="utf-8"
  )
  (tmp_path / ".context-schema-version").write_text(
      generate_views.CONTEXT_SCHEMA_VERSION, encoding="utf-8"
  )

  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    assert not generate_views.check_context_version()
    (tmp_path / "segment_metric_compatibility.yaml").write_text(
        "{}\n", encoding="utf-8"
    )
    assert generate_views.check_context_version()


@pytest.mark.asyncio
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_save_view_yaml(mock_get_view_json, tmp_path):
  """Tests the save_view_yaml function."""
  mock_get_view_json.return_value = _view_json("campaign")

  await generate_views.save_view_yaml("campaign", path=str(tmp_path))

  output = yaml.safe_load((tmp_path / "campaign.yaml").read_text())
  assert output["name"] == "campaign"
  assert output["attributes"] == ["campaign.id"]


@mock.patch.object(generate_views, "check_context_version", return_value=True)
def test_should_regenerate_views_detects_missing_files(mock_check_version):
  """Matching markers still trigger when a declared view is absent."""
  with mock.patch.object(
      generate_views,
      "_read_views_manifest",
      return_value={"ad_group", "campaign"},
  ):
    assert generate_views.should_regenerate_views(
        ["campaign", "ad_group"], {"campaign"}
    )
    assert not generate_views.should_regenerate_views(
        ["campaign", "ad_group"], {"campaign", "ad_group"}
    )
  assert mock_check_version.call_count == 2


@mock.patch.object(generate_views, "check_context_version", return_value=True)
def test_should_regenerate_views_detects_stale_manifest(_):
  """Complete view files still trigger until a run fully succeeds."""
  present_views = {"campaign", "ad_group"}
  with mock.patch.object(
      generate_views, "_read_views_manifest", return_value=None
  ):
    assert generate_views.should_regenerate_views(
        ["campaign", "ad_group"], present_views
    )
  with mock.patch.object(
      generate_views, "_read_views_manifest", return_value={"campaign"}
  ):
    assert generate_views.should_regenerate_views(
        ["campaign", "ad_group"], present_views
    )


@pytest.mark.asyncio
@mock.patch.object(generate_views, "check_context_version", return_value=True)
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_update_views_yaml_regenerates_all_when_view_file_missing(
    mock_get_view_json, _, tmp_path
):
  """A missing declared file refreshes every view and writes markers."""
  _write_context(tmp_path, ["campaign", "ad_group"], {"campaign"})
  mock_get_view_json.side_effect = [
      _view_json("campaign"),
      _view_json("ad_group"),
  ]

  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    await generate_views.update_views_yaml()

  assert mock_get_view_json.await_count == 2
  assert (tmp_path / "views" / "ad_group.yaml").is_file()
  assert (tmp_path / "fields.yaml").is_file()
  assert (tmp_path / "segment_metric_compatibility.yaml").is_file()
  assert (tmp_path / ".api-version").read_text() == "v24"
  assert (
      tmp_path / ".mcp-server-version"
  ).read_text() == generate_views.MCP_SERVER_VERSION
  assert (
      tmp_path / ".context-schema-version"
  ).read_text() == generate_views.CONTEXT_SCHEMA_VERSION
  assert (tmp_path / ".views-manifest").read_text() == "ad_group\ncampaign"


@pytest.mark.asyncio
@mock.patch.object(generate_views, "check_context_version", return_value=True)
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_update_views_yaml_skips_complete_matching_context(
    mock_get_view_json, _, tmp_path
):
  """Matching markers and complete view files avoid endpoint requests."""
  _write_context(
      tmp_path,
      ["campaign", "ad_group"],
      {"campaign", "ad_group"},
      manifest_views=["campaign", "ad_group"],
  )

  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    await generate_views.update_views_yaml()

  mock_get_view_json.assert_not_awaited()
  assert not (tmp_path / "fields.yaml").exists()
  assert not (tmp_path / "segment_metric_compatibility.yaml").exists()


@pytest.mark.asyncio
@mock.patch.object(generate_views, "check_context_version", return_value=False)
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_update_views_yaml_partial_failure_preserves_retry_state(
    mock_get_view_json, _, tmp_path, caplog
):
  """Successful views persist, but aggregate files and markers wait for all."""
  _write_context(tmp_path, ["campaign", "ad_group"])

  async def fetch_view(view, _):
    if view == "ad_group":
      raise httpx.TimeoutException("request timed out")
    return _view_json(view)

  mock_get_view_json.side_effect = fetch_view
  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    with caplog.at_level(logging.WARNING):
      await generate_views.update_views_yaml()

  assert (tmp_path / "views" / "campaign.yaml").is_file()
  assert not (tmp_path / "views" / "ad_group.yaml").exists()
  assert not (tmp_path / "fields.yaml").exists()
  assert not (tmp_path / ".api-version").exists()
  assert not (tmp_path / ".mcp-server-version").exists()
  assert not (tmp_path / ".context-schema-version").exists()
  assert not (tmp_path / ".views-manifest").exists()
  assert caplog.messages == ["Failed to refresh reporting views: ad_group"]


@pytest.mark.asyncio
@mock.patch.object(generate_views, "check_context_version", return_value=True)
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_update_views_yaml_retries_after_partial_failure(
    mock_get_view_json, _, tmp_path
):
  """Complete view files without a manifest still refetch until success."""
  _write_context(tmp_path, ["campaign", "ad_group"], {"campaign", "ad_group"})
  mock_get_view_json.side_effect = [
      _view_json("campaign"),
      _view_json("ad_group"),
  ]

  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    await generate_views.update_views_yaml()

  assert mock_get_view_json.await_count == 2
  assert (tmp_path / "fields.yaml").is_file()
  assert (tmp_path / ".views-manifest").read_text() == "ad_group\ncampaign"

  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    await generate_views.update_views_yaml()

  assert mock_get_view_json.await_count == 2


@pytest.mark.asyncio
@mock.patch.object(generate_views, "check_context_version", return_value=False)
@mock.patch.object(
    generate_views, "get_view_json", new_callable=mock.AsyncMock
)
async def test_update_views_yaml_treats_cancellation_as_failure(
    mock_get_view_json, _, tmp_path, caplog
):
  """A cancelled fetch takes the warning path instead of crashing."""
  _write_context(tmp_path, ["campaign", "ad_group"])

  async def fetch_view(view, _):
    if view == "ad_group":
      raise asyncio.CancelledError()
    return _view_json(view)

  mock_get_view_json.side_effect = fetch_view
  with mock.patch.object(generate_views, "CONTEXT_PATH", str(tmp_path)):
    with caplog.at_level(logging.WARNING):
      await generate_views.update_views_yaml()

  assert not (tmp_path / "fields.yaml").exists()
  assert caplog.messages == ["Failed to refresh reporting views: ad_group"]


def test_refresh_view_docs_for_startup_bounds_slow_updates(caplog):
  """A hung documentation refresh cannot block server boot."""

  async def hang_forever():
    await asyncio.sleep(3600)

  with mock.patch.object(
      generate_views, "update_views_yaml", side_effect=hang_forever
  ):
    with caplog.at_level(logging.WARNING):
      generate_views.refresh_view_docs_for_startup(timeout_seconds=0.05)

  assert "Unable to refresh reporting view documentation" in caplog.text
