# Google Ads MCP Server 🚀

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastMCP 3.1+](https://img.shields.io/badge/FastMCP-3.1+-green.svg)](https://github.com/jlowin/fastmcp)
[![Google Ads API v23](https://img.shields.io/badge/Google%20Ads%20API-v23-red.svg)](https://developers.google.com/google-ads/api/docs/start)

**A powerful MCP server that bridges LLMs with the Google Ads API — 64 tools for querying, managing, and optimizing your ad accounts through natural language.**

> Ask Claude or Gemini to "show me my top campaigns this month" or "pause that underperforming ad group" — and it just works.

**This is not an officially supported Google product.**

## ✨ Features

- 📊 **Full GAQL Support** — Run any Google Ads Query Language query with automatic field formatting
- 🔧 **64 Tools** — Read, write, and manage campaigns, ad groups, ads, keywords, labels, budgets, and more
- 📖 **Built-in Docs** — GAQL syntax reference, reporting field docs, and a tool guide available as tools
- 🔍 **Smart Tool Search** — BM25-powered tool discovery surfaces relevant tools automatically
- 🔒 **Mutation Safety** — Mutation tools are hidden by default; unlock them per-session when needed
- 📊 **Curated Reporting** — Device, geographic, impression share, quality scores, quality score summaries, conversion goals, RSA ad strength, conversion actions, and audience performance
- ⚡ **Response Caching** — Docs, tool guide, and campaign context are cached to reduce latency and token usage
- 📈 **Optimization** — Recommendations, optimization score, bid/budget simulations, and search term analysis
- 🏎️ **Performance Max** — Asset diagnostics, top combinations, and placement insights
- 🔑 **Keyword Research** — Generate keyword ideas with search volume and competition data
- 🏷️ **Label Management** — Create, apply, and remove labels across campaigns and ad groups
- 🚫 **Negative Keywords** — Full shared set and campaign-level negative keyword management
- 💡 **Smart Campaigns** — Get AI-suggested keyword themes, ad copy, and budget recommendations
- 🖥️ **Works Everywhere** — Claude Code, Claude Desktop, Gemini CLI, or any MCP client

## 📋 Available Tools (64)

### 🔍 Query & Discovery

| Tool | Description |
|------|-------------|
| `execute_gaql` | Run any GAQL query with formatted results |
| `list_accessible_accounts` | List all Google Ads accounts you can access |

### 📖 Docs & Tool Guidance

| Tool | Description |
|------|-------------|
| `get_tool_guide` | Compact map of tools and when to use them |
| `get_gaql_doc` | Compact GAQL syntax reference |
| `get_reporting_view_doc` | Reporting view names or detailed view metadata |
| `get_reporting_fields_doc` | Detailed docs for specific reporting query fields |
| `search_google_ads_fields` | Live field metadata search for GAQL query building |

### 🔒 Session Controls

| Tool | Description |
|------|-------------|
| `get_tool_visibility_profile` | Check whether mutation tools are unlocked |
| `unlock_mutation_tools` | Reveal mutation tools for the current session |
| `lock_mutation_tools` | Hide mutation tools for the current session |

### 📈 Optimization & Recommendations

| Tool | Description |
|------|-------------|
| `get_optimization_score_summary` | Account optimization score and uplift by type |
| `list_recommendations` | Open recommendations filtered by type or campaign |
| `apply_recommendations` | Apply existing recommendations |
| `dismiss_recommendations` | Dismiss existing recommendations |
| `list_recommendation_subscriptions` | Current recommendation auto-apply subscriptions |
| `create_recommendation_subscription` | Create a paused or enabled subscription |
| `set_recommendation_subscription_status` | Pause or enable a subscription |

### 🔎 Search Terms

| Tool | Description |
|------|-------------|
| `list_campaign_search_term_insights` | Insight categories and search terms for a single campaign |
| `list_customer_search_term_insights` | Account-level insight categories and search terms |
| `analyze_search_terms` | Heuristic exact-match and negative keyword candidates |

### 📊 Simulations

| Tool | Description |
|------|-------------|
| `list_campaign_simulations` | Campaign-level bid/budget simulations |
| `list_ad_group_simulations` | Ad-group-level simulations |
| `list_ad_group_criterion_simulations` | Keyword-level CPC bid simulations |

### 🕐 Change History

| Tool | Description |
|------|-------------|
| `list_change_statuses` | Changed resources and last change timestamps |
| `list_change_events` | Granular change events with field-level detail |

### 🏎️ Performance Max

| Tool | Description |
|------|-------------|
| `list_asset_group_assets` | Asset links and serving diagnostics |
| `list_asset_group_top_combinations` | Top served asset combinations |
| `list_performance_max_placements` | Placement names and impression counts |

### 📊 Reporting

| Tool | Description |
|------|-------------|
| `list_device_performance` | Campaign performance segmented by device |
| `list_geographic_performance` | Campaign performance segmented by geography |
| `list_impression_share` | Campaign impression share metrics |
| `get_campaign_conversion_goals` | Conversion goals and custom goal config for a campaign |
| `list_keyword_quality_scores` | Keyword quality score diagnostics |
| `summarize_keyword_quality_scores` | Quality score distribution summary across campaigns |
| `list_rsa_ad_strength` | RSA ad strength diagnostics |
| `list_conversion_actions` | Conversion action configuration |
| `list_audience_performance` | Audience performance at campaign or ad group scope |

### 📢 Campaign Management

| Tool | Description |
|------|-------------|
| `set_campaign_status` | Set a campaign to PAUSED or ENABLED |
| `update_campaign_budget` | Change a campaign's daily budget |

### 👥 Ad Group Management

| Tool | Description |
|------|-------------|
| `set_ad_group_status` | Set an ad group to PAUSED or ENABLED |
| `update_ad_group_bid` | Update an ad group's CPC bid |

### 📝 Ad Management

| Tool | Description |
|------|-------------|
| `set_ad_status` | Set an ad to PAUSED or ENABLED |

### 🔑 Keyword Management

| Tool | Description |
|------|-------------|
| `set_keyword_status` | Set a keyword to PAUSED or ENABLED |
| `update_keyword_bid` | Update a keyword's CPC bid |
| `generate_keyword_ideas` | Research new keywords with volume & competition data |

### 🚫 Negative Keywords

| Tool | Description |
|------|-------------|
| `list_shared_sets` | List negative keyword shared sets |
| `create_shared_set` | Create a new shared negative keyword set |
| `delete_shared_set` | Delete a shared set |
| `list_shared_set_keywords` | List keywords in a shared set |
| `add_shared_set_keywords` | Add keywords to a shared set |
| `remove_shared_set_keywords` | Remove keywords from a shared set |
| `list_campaign_shared_sets` | List shared sets attached to a campaign |
| `attach_shared_set_to_campaign` | Attach a shared set to a campaign |
| `detach_shared_set_from_campaign` | Detach a shared set from a campaign |
| `list_campaign_negative_keywords` | List campaign-level negative keywords |
| `add_campaign_negative_keywords` | Add campaign-level negative keywords |
| `remove_campaign_negative_keywords` | Remove campaign-level negative keywords |

### 🏷️ Labels

| Tool | Description |
|------|-------------|
| `create_label` | Create a new label |
| `delete_label` | Delete a label |
| `manage_campaign_labels` | Apply or remove a label to/from campaigns |
| `manage_ad_group_labels` | Apply or remove a label to/from ad groups |

### 💡 Smart Campaign Suggestions

| Tool | Description |
|------|-------------|
| `suggest_keyword_themes` | Get keyword theme suggestions for a business |
| `suggest_smart_campaign_ad` | Get AI-suggested headlines and descriptions |
| `suggest_smart_campaign_budget` | Get low/recommended/high budget options |

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pipx`
- A `google-ads.yaml` file with your Google Ads API credentials

### 1. Get Your Credentials

You need a `google-ads.yaml` with these keys:

```yaml
client_id: YOUR_CLIENT_ID
client_secret: YOUR_CLIENT_SECRET
refresh_token: YOUR_REFRESH_TOKEN
developer_token: YOUR_DEVELOPER_TOKEN
login_customer_id: YOUR_MCC_ID  # optional but recommended
```

Don't have one? Generate it using the [authentication example](https://github.com/googleads/google-ads-python/blob/main/examples/authentication/generate_user_credentials.py) from the `google-ads-python` library.

### 2. Install & Run

#### Option A: Claude Code (recommended)

```bash
# Install from repo
claude mcp add --transport stdio GoogleAds \
  --env GOOGLE_ADS_CREDENTIALS=PATH_TO_YAML \
  -- pipx run --spec git+https://github.com/jeffreywlai/google_ads_mcp.git run-mcp-server
```

Or from a local clone:

```bash
claude mcp add --transport stdio GoogleAds \
  --env GOOGLE_ADS_CREDENTIALS=PATH_TO_YAML \
  -- uv run --directory /path/to/google_ads_mcp -m ads_mcp.stdio
```

Type `/mcp` in Claude Code to verify it's connected.

#### Option B: Gemini CLI

Add to your Gemini configuration:

```json5
{
  "mcpServers": {
    "GoogleAds": {
      "command": "pipx",
      "args": [
        "run", "--spec",
        "git+https://github.com/google-marketing-solutions/google_ads_mcp.git",
        "run-mcp-server"
      ],
      "env": {
        "GOOGLE_ADS_CREDENTIALS": "PATH_TO_YAML"
      },
      "timeout": 30000
    }
  }
}
```

#### Option C: Direct Launch

```bash
uv run -m ads_mcp.server
```

## 💬 Usage Examples

Once connected, just talk naturally:

### Querying

```
"List all my campaigns and their status"
"Show me the top 10 keywords by conversions this month"
"What's my total spend across all campaigns last week?"
"Show me ad performance broken down by device"
```

### Managing

```
"Pause campaign 123456789"
"Set the daily budget for campaign 123 to $50"
"Update the CPC bid for ad group 456 to $2.50"
"Enable the ad I just paused"
```

### Keywords

```
"Generate keyword ideas for 'digital marketing agency'"
"Add 'free' and 'cheap' as negative keywords to my shared set"
"What negative keyword lists are attached to campaign 123?"
```

### Labels

```
"Create a label called 'Q1 Test'"
"Apply the label to campaigns 111, 222, and 333"
"Remove the 'Old' label from all ad groups"
```

### Optimization

```
"What's my optimization score?"
"Show me recommendations for campaign 123"
"Apply the top recommendation"
"What bid simulations are available for my campaigns?"
```

### Search Terms

```
"What search terms are triggering my ads?"
"Analyze search terms and find negative keyword candidates"
"Show me search term insights for campaign 123"
```

### Reporting

```
"Show me performance by device for the last 30 days"
"What's my impression share across campaigns?"
"List keyword quality scores for campaign 123"
"Summarize quality scores across all my campaigns"
"What are the conversion goals for campaign 123?"
"Show me RSA ad strength for my ad groups"
"What conversion actions do I have set up?"
"Show audience performance at the ad group level"
"Break down performance by geography"
```

### Performance Max

```
"List asset group assets and their serving status"
"Show me the top asset combinations for my PMax campaign"
"What placements are my PMax ads showing on?"
```

### Change History

```
"What changed in my account in the last 7 days?"
"Show me change events for campaign 123"
```

### Smart Campaigns

```
"Suggest keyword themes for Joe's Plumbing"
"Generate ad headlines for my bakery website"
"What budget should I set for a smart campaign targeting plumbers in the US?"
```

## 🏗️ Project Structure

```
google_ads_mcp/
├── ads_mcp/
│   ├── server.py              # Server entry point (Gemini / SSE)
│   ├── stdio.py               # Server entry point (Claude Code / stdio)
│   ├── coordinator.py         # Shared FastMCP instance + search/visibility config
│   ├── tooling.py             # Shared tool decorators and annotations
│   ├── utils.py               # Shared utility constants
│   ├── scripts/
│   │   └── generate_views.py  # Auto-update reporting view YAMLs
│   ├── tools/
│   │   ├── api.py             # Core: execute_gaql, list_accessible_accounts
│   │   ├── docs.py            # GAQL & reporting docs, tool guide, visibility controls
│   │   ├── campaigns.py       # Pause, resume, update budget
│   │   ├── ad_groups.py       # Pause, enable, update bid
│   │   ├── ads.py             # Pause, enable ads
│   │   ├── keywords.py        # Pause, enable, update bid
│   │   ├── negatives.py       # Shared sets & campaign negatives
│   │   ├── labels.py          # Label CRUD & assignment
│   │   ├── keyword_planner.py # Keyword research
│   │   ├── smart_campaigns.py # Smart campaign suggestions
│   │   ├── recommendations.py # Optimization score & recommendations
│   │   ├── search_terms.py    # Search term insights & analysis
│   │   ├── simulations.py     # Bid & budget simulations
│   │   ├── changes.py         # Change history auditing
│   │   ├── reporting.py       # Curated reporting tools (device, geo, impression share, etc.)
│   │   ├── performance_max.py # PMax asset & placement diagnostics
│   │   ├── _gaql.py           # Shared GAQL query helpers
│   │   └── _campaign_context.py # Cached campaign status/spend context
│   └── context/               # GAQL docs, reporting view YAMLs, tool guide
├── tests/                     # Mirrors source structure
├── pyproject.toml
└── README.md
```

## 🛠️ Development

```bash
uv sync                          # Install dependencies
uv run pytest                    # Run tests
uv run pyink .                   # Format (Google style)
uv run pylint ads_mcp tests      # Lint
```

### Adding a Tool

1. Create or edit a file in `ads_mcp/tools/`
2. Import `from ads_mcp.coordinator import mcp_server as mcp`
3. Decorate with `@mcp.tool()`
4. Register in `ads_mcp/server.py`
5. Add tests in `tests/tools/`

See [CONTRIBUTING.md](CONTRIBUTING.md) for full details.

## ⚠️ Disclaimer

Copyright Google LLC. Supported by Google LLC and/or its affiliate(s). This solution, including any related sample code or data, is made available on an "as is," "as available," and "with all faults" basis, solely for illustrative purposes, and without warranty or representation of any kind. This solution is experimental, unsupported and provided solely for your convenience. Your use of it is subject to your agreements with Google, as applicable, and may constitute a beta feature as defined under those agreements. To the extent that you make any data available to Google in connection with your use of the solution, you represent and warrant that you have all necessary and appropriate rights, consents and permissions to permit Google to use and process that data. By using any portion of this solution, you acknowledge, assume and accept all risks, known and unknown, associated with its usage and any processing of data by Google, including with respect to your deployment of any portion of this solution in your systems, or usage in connection with your business, if at all. With respect to the entrustment of personal information to Google, you will verify that the established system is sufficient by checking Google's privacy policy and other public information, and you agree that no further information will be provided by Google.

## 📄 License

Licensed under the [Apache License 2.0](LICENSE).

## 📬 Contact

Questions, suggestions, or feedback? [Open an issue](../../issues).

---

**Built with [FastMCP](https://github.com/jlowin/fastmcp) and [Google Ads API v23](https://developers.google.com/google-ads/api/docs/start)**
