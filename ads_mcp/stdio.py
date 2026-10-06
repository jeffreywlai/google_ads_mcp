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

"""The server for the Google Ads API MCP."""

from ads_mcp.coordinator import mcp_server
from ads_mcp.registry import TOOL_MODULES
from ads_mcp.tools import api
from ads_mcp.diagnostics import enable_diagnostics
from ads_mcp.scripts.generate_views import refresh_view_docs_for_startup

import dotenv

dotenv.load_dotenv()


tools = TOOL_MODULES


def main():
  """Initializes and runs the MCP server."""
  enable_diagnostics(mcp_server, "stdio")
  refresh_view_docs_for_startup()  # Check and update docs resource
  api.get_ads_client()  # Check Google Ads credentials
  mcp_server.run(
      transport="stdio",
      show_banner=False,
  )  # Initialize and run the server


if __name__ == "__main__":
  main()
