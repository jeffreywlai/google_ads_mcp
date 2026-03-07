# Google Ads MCP Server

Python MCP server (FastMCP) bridging LLMs with the Google Ads API. Uses Google Ads API v23.

## Commands

```bash
uv sync                          # Install dependencies
uv run -m ads_mcp.server         # Start server
uv run pytest                    # Run tests
uv run pytest tests/tools/test_api.py -v  # Run specific test file
uv run pyink .                   # Format (Google style)
uv run pylint ads_mcp tests      # Lint
```

## Project Structure

- `ads_mcp/server.py` — Server entry point, imports tool modules
- `ads_mcp/coordinator.py` — Shared `mcp_server` FastMCP instance
- `ads_mcp/tools/` — Tool modules (api.py, docs.py, negatives.py)
- `ads_mcp/context/` — GAQL docs and reporting view YAMLs
- `tests/` — Mirrors source structure; tests/tools/ for tool tests

## Code Style

- Follow the Google Python Style Guide
- 2-space indentation (no tabs)
- 79-character line length
- Use `pyink` for formatting — always run `uv run pyink <file>` on new/changed files
- Double-quote strings (majority quotes enforced by pyink)

## Adding Tools

1. Create a new file in `ads_mcp/tools/` or add to an existing one
2. Import the shared server: `from ads_mcp.coordinator import mcp_server as mcp`
3. Decorate functions with `@mcp.tool()`
4. Import `get_ads_client` from `ads_mcp.tools.api` for API access
5. Register the module in `ads_mcp/server.py` (import + add to `tools` list)

### Tool patterns (match existing code in api.py)

- All tools take `customer_id: str` and optional `login_customer_id: str | None = None`
- Set `ads_client.login_customer_id = login_customer_id` when provided
- Use GAQL via `ads_service.search_stream()` for reads
- Use service-specific `.mutate_*()` methods for writes
- Catch `GoogleAdsException` and raise `ToolError` with formatted messages:
  ```python
  except GoogleAdsException as e:
    raise ToolError("\n".join(str(i) for i in e.failure.errors)) from e
  ```
- Return dicts with descriptive keys
- Every tool needs a docstring with Args/Returns (FastMCP uses these as tool descriptions)

## Testing

- Use `unittest.mock` with `pytest`
- Mock `get_ads_client` to avoid needing real credentials
- Test tools by calling them directly: `negatives.list_shared_sets("123")`
- New features must include corresponding tests in `tests/`
- Test file naming: `tests/tools/test_<module>.py`

## Credentials

- Requires `google-ads.yaml` with `client_id`, `client_secret`, `refresh_token`, `developer_token`
- Path configurable via `GOOGLE_ADS_CREDENTIALS` env var (defaults to project root)
- `USE_GOOGLE_OAUTH_ACCESS_TOKEN` env var enables OAuth token verification

## Dependencies

- Managed with `uv` (see `pyproject.toml`)
- `google-ads==29.1.0` (Google Ads API v23)
- `fastmcp>=3.0.2`
