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

"""The coordinator for the Google Ads API MCP."""

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform

from ads_mcp.tooling import MUTATE_TAG
from ads_mcp.tooling import compact_search_result_serializer

# Initialize FastMCP server
mcp_server = FastMCP(
    name="Google Ads API",
    instructions=(
        "Google Ads API MCP server. Use these tools to manage Google"
        " Ads campaigns, execute GAQL reporting queries, manage"
        " negative keyword lists and shared sets, and access Google"
        " Ads API documentation. Requires a configured google-ads.yaml"
        " credentials file."
    ),
    mask_error_details=True,
    transforms=[
        BM25SearchTransform(
            max_results=8,
            always_visible=[
                "get_tool_guide",
                "list_accessible_accounts",
                "execute_gaql",
                "get_tool_visibility_profile",
                "unlock_mutation_tools",
                "lock_mutation_tools",
            ],
            search_result_serializer=compact_search_result_serializer,
        )
    ],
)

mcp_server.disable(tags={MUTATE_TAG}, components={"tool"})
