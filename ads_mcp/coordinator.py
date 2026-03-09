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
        "Google Ads API MCP server. Use search_tools first to find the"
        " smallest dedicated tool. Most Google Ads tools take customer_id"
        " and optional login_customer_id, so focus on the other args when"
        " choosing a tool. Use get_tool_guide(topic) only when search"
        " results are ambiguous. Use execute_gaql only for custom read"
        " queries not covered by dedicated tools. Mutation tools stay"
        " hidden until unlock_mutation_tools. Requires a configured"
        " google-ads.yaml credentials file."
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
