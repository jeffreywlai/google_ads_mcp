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

"""Tests for stdio.py."""

from unittest import mock

from ads_mcp import server
from ads_mcp import stdio
import httpx


@mock.patch("ads_mcp.stdio.mcp_server")
@mock.patch("ads_mcp.stdio.api")
@mock.patch("ads_mcp.stdio.refresh_view_docs_for_startup")
@mock.patch("builtins.print")
def test_main(mock_print, mock_refresh_views, mock_api, mock_mcp_server):
  """Tests main function."""
  stdio.main()

  mock_refresh_views.assert_called_once_with()
  mock_api.get_ads_client.assert_called_once()
  mock_print.assert_not_called()
  mock_mcp_server.run.assert_called_once_with(
      transport="stdio", show_banner=False
  )


def test_stdio_and_server_register_same_tool_modules():
  """Tests that stdio and streamable-http entrypoints stay in sync."""
  assert {module.__name__ for module in stdio.tools} == {
      module.__name__ for module in server.tools
  }


def test_script_targets_are_importable_callables():
  """Tests that both installed script targets resolve to callables."""
  assert callable(stdio.main)
  assert callable(server.main)


@mock.patch("ads_mcp.stdio.mcp_server")
@mock.patch("ads_mcp.stdio.api")
@mock.patch(
    "ads_mcp.scripts.generate_views.update_views_yaml",
    new_callable=mock.AsyncMock,
)
def test_main_continues_when_view_update_times_out(
    mock_update_views, mock_api, mock_mcp_server, caplog
):
  """A documentation timeout does not prevent the stdio server from booting."""
  mock_update_views.side_effect = httpx.TimeoutException("request timed out")

  stdio.main()

  mock_api.get_ads_client.assert_called_once_with()
  mock_mcp_server.run.assert_called_once_with(
      transport="stdio", show_banner=False
  )
  assert "Unable to refresh reporting view documentation" in caplog.text
