# Google Ads MCP Server 🚀

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastMCP 3.0+](https://img.shields.io/badge/FastMCP-3.0+-green.svg)](https://github.com/jlowin/fastmcp)
[![Google Ads API v23](https://img.shields.io/badge/Google%20Ads%20API-v23-red.svg)](https://developers.google.com/google-ads/api/docs/start)

**A powerful MCP server that bridges LLMs with the Google Ads API — 32 tools for querying, managing, and optimizing your ad accounts through natural language.**

> Ask Claude or Gemini to "show me my top campaigns this month" or "pause that underperforming ad group" — and it just works.

**This is not an officially supported Google product.**

## ✨ Features

- 📊 **Full GAQL Support** — Run any Google Ads Query Language query with automatic field formatting
- 🔧 **32 Tools** — Read, write, and manage campaigns, ad groups, ads, keywords, labels, budgets, and more
- 📖 **Built-in Docs** — GAQL syntax reference and reporting field docs available as tools (the LLM teaches itself)
- 🔑 **Keyword Research** — Generate keyword ideas with search volume and competition data
- 🏷️ **Label Management** — Create, apply, and remove labels across campaigns and ad groups
- 🚫 **Negative Keywords** — Full shared set and campaign-level negative keyword management
- 💡 **Smart Campaigns** — Get AI-suggested keyword themes, ad copy, and budget recommendations
- 🖥️ **Works Everywhere** — Claude Code, Claude Desktop, Gemini CLI, or any MCP client

## 📋 Available Tools (32)

### 🔍 Query & Discovery

| Tool | Description |
|------|-------------|
| `execute_gaql` | Run any GAQL query with formatted results |
| `list_accessible_accounts` | List all Google Ads accounts you can access |
| `get_gaql_doc` | Get compact GAQL syntax reference |
| `get_reporting_view_doc` | Get available reporting views and their fields |
| `get_reporting_fields_doc` | Get detailed field documentation |

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
│   ├── coordinator.py         # Shared FastMCP instance
│   ├── tools/
│   │   ├── api.py             # Core: execute_gaql, list_accessible_accounts
│   │   ├── campaigns.py       # Pause, resume, update budget
│   │   ├── ad_groups.py       # Pause, enable, update bid
│   │   ├── ads.py             # Pause, enable ads
│   │   ├── keywords.py        # Pause, enable, update bid
│   │   ├── negatives.py       # Shared sets & campaign negatives
│   │   ├── labels.py          # Label CRUD & assignment
│   │   ├── keyword_planner.py # Keyword research
│   │   ├── smart_campaigns.py # Smart campaign suggestions
│   │   └── docs.py            # GAQL & reporting docs
│   └── context/               # GAQL docs & reporting view YAMLs
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
