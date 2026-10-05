# Google Ads MCP Server

Python MCP server for Google Ads API release 25.2. Minor releases use the
`v25` namespace. Python 3.12, uv, google-ads>=33.0.0,<34.0.0, and
FastMCP>=3.4.8,<3.5 (private coordinator APIs; upgrade deliberately).

## Commands

```bash
uv sync --locked
uv run pyink --check .
uv run pylint ads_mcp tests --fail-under=9.5
uv run pytest -q
uv run -m ads_mcp.stdio
run-mcp-server-http
```

CI is the deploy gate: users can install directly from Git with uvx.
Do not claim deployment readiness without a green locked install and checks.

## Structure and tools

- ads_mcp/registry.py is the single public-tool module registry used by both
  transports. Register new modules there and in tests/test_server_integrity.py.
- ads_mcp/api_version.py deliberately selects API major namespace and minor
  release. SDK 33 provides the v25.2 additions.
- ads_mcp/tooling.py defines read, remote mutation, local write, local read,
  and session-control decorators; do not use a bare @mcp.tool decorator.
- ads_mcp/tools/_gaql.py handles quoting, input validation, query preflight,
  dates, pagination and native field compatibility.
- ads_mcp/tools/_service.py parses strict native protobuf JSON and exposes
  installed service schemas. Validate requests before creating a client.
- ads_mcp/tools/api.py owns account-scoped clients, centralized errors,
  paging, exports and bounded exact-result delivery.
- ads_mcp/context contains generated field/view compatibility metadata.
  Regenerate after changing views.yaml, API version or MCP package version.
  Commit successful markers and .views-manifest only after all fetches pass.
- tests/tools mirrors public modules. Every behavior change needs focused
  offline regression coverage; mock get_ads_client, no credentials/network.

Wrap each API call and any result-stream iteration tightly in
`with handle_google_ads_errors():`. Do not wrap the entire tool or copy old
manual GoogleAdsException handlers. Never retry a mutation. Read retries
remain exclusively in run_gaql_query. Validate IDs, lists, enums, limits and
final fields before service calls. Use run_gaql_query_page and
build_paginated_list_response for paginated reads, retaining snapshot_token.
Bound new variable-sized responses while preserving an exact export path.
Tools require Args/Returns docstrings and the correct effect decorator.

## Style and credentials

Google Python style:2 spaces,79 characters,double quotes,pyink. Keep changes
focused. Live tests are opt-in with GOOGLE_ADS_RUN_LIVE_TESTS=1.
GOOGLE_ADS_CREDENTIALS chooses the YAML file (default project google-ads.yaml).
Required:client_id,client_secret,refresh_token,developer_token; optional
login_customer_id. USE_GOOGLE_OAUTH_ACCESS_TOKEN enables access-token auth.
GOOGLE_ADS_MCP_EXPORT_DIR constrains explicit exports (default OS tempdir).
Keep credentials out of source, logs and reports.
