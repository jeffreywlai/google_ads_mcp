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
import asyncio
import os
from urllib.parse import urlparse

from ads_mcp.coordinator import mcp_server
from ads_mcp.scripts.generate_views import update_views_yaml
from ads_mcp.tools import ad_groups
from ads_mcp.tools import ads
from ads_mcp.tools import api
from ads_mcp.tools import audiences
from ads_mcp.tools import campaigns
from ads_mcp.tools import changes
from ads_mcp.tools import conversions
from ads_mcp.tools import docs
from ads_mcp.tools import keyword_planner
from ads_mcp.tools import keywords
from ads_mcp.tools import labels
from ads_mcp.tools import negatives
from ads_mcp.tools import performance_max
from ads_mcp.tools import reporting
from ads_mcp.tools import recommendations
from ads_mcp.tools import search_terms
from ads_mcp.tools import simulations
from ads_mcp.tools import smart_campaigns

import dotenv
import fastmcp
from fastmcp.server.auth.redirect_validation import DEFAULT_LOCALHOST_PATTERNS
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from fastmcp.server.event_store import EventStore
from fastmcp.server.middleware.ping import PingMiddleware
import uvicorn


dotenv.load_dotenv()


tools = [
    ad_groups,
    ads,
    api,
    audiences,
    campaigns,
    changes,
    conversions,
    docs,
    keyword_planner,
    keywords,
    labels,
    negatives,
    performance_max,
    reporting,
    recommendations,
    search_terms,
    simulations,
    smart_campaigns,
]


def _parse_csv_env(env_var: str) -> list[str] | None:
  """Parses a comma-separated env var into a compact list."""
  raw_value = os.getenv(env_var)
  if raw_value is None:
    return None
  return [item.strip() for item in raw_value.split(",") if item.strip()]


def _is_loopback_base_url(base_url: str) -> bool:
  """Returns whether a base URL points at a loopback/local dev host."""
  parsed_url = urlparse(base_url)
  hostname = parsed_url.hostname
  if hostname is None:
    hostname = urlparse(f"http://{base_url}").hostname
  return hostname in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _get_allowed_client_redirect_uris(base_url: str) -> list[str]:
  """Builds the OAuth redirect allowlist for GoogleProvider."""
  configured_uris = _parse_csv_env(
      "FASTMCP_SERVER_AUTH_ALLOWED_CLIENT_REDIRECT_URIS"
  )
  if configured_uris:
    return configured_uris

  if _is_loopback_base_url(base_url):
    return list(DEFAULT_LOCALHOST_PATTERNS)

  raise ValueError(
      "Set FASTMCP_SERVER_AUTH_ALLOWED_CLIENT_REDIRECT_URIS to a "
      "non-empty comma-separated list when FASTMCP_SERVER_BASE_URL is "
      "not loopback."
  )


def _build_auth_provider():
  """Builds the configured auth provider, if any."""
  auth_provider = None
  if os.getenv("USE_GOOGLE_OAUTH_ACCESS_TOKEN"):
    auth_provider = GoogleTokenVerifier()

  client_id = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID")
  client_secret = os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET")
  if client_id and client_secret:
    base_url = os.getenv("FASTMCP_SERVER_BASE_URL", "http://localhost:8000")
    auth_provider = GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        required_scopes=["https://www.googleapis.com/auth/adwords"],
        allowed_client_redirect_uris=_get_allowed_client_redirect_uris(
            base_url
        ),
        forward_resource=True,
    )

  return auth_provider


def _get_positive_int_env(
    env_var: str,
    *,
    default: int | None = None,
) -> int | None:
  """Parses an optional positive integer env var with clear errors."""
  raw_value = os.getenv(env_var)
  if raw_value is None:
    return default

  try:
    parsed_value = int(raw_value)
  except ValueError as e:
    raise ValueError(f"{env_var} must be a positive integer.") from e

  if parsed_value <= 0:
    raise ValueError(f"{env_var} must be a positive integer.")

  return parsed_value


def _ensure_ping_middleware() -> None:
  """Adds a single PingMiddleware instance for streamable-http sessions."""
  interval_ms = _get_positive_int_env(
      "FASTMCP_SERVER_PING_INTERVAL_MS", default=30000
  )
  for middleware in mcp_server.middleware:
    if isinstance(middleware, PingMiddleware):
      middleware.interval_ms = interval_ms
      return

  mcp_server.add_middleware(PingMiddleware(interval_ms=interval_ms))


def _get_retry_interval_ms() -> int | None:
  """Returns an optional streamable-http retry interval from env."""
  return _get_positive_int_env("FASTMCP_STREAMABLE_HTTP_RETRY_INTERVAL_MS")


def _build_streamable_http_app():
  """Builds a resumable streamable-http app with compact defaults."""
  _ensure_ping_middleware()
  return mcp_server.http_app(
      transport="streamable-http",
      event_store=EventStore(),
      retry_interval=_get_retry_interval_ms(),
  )


def _serve_streamable_http_app(app) -> None:
  """Serves the FastMCP streamable-http app via uvicorn."""
  config = uvicorn.Config(
      app,
      host=fastmcp.settings.host,
      port=fastmcp.settings.port,
      timeout_graceful_shutdown=2,
      lifespan="on",
      ws="websockets-sansio",
      log_level=fastmcp.settings.log_level.lower(),
  )
  uvicorn.Server(config).run()


def main():
  """Initializes and runs the MCP server."""
  asyncio.run(update_views_yaml())  # Check and update docs resource
  api.get_ads_client()  # Check Google Ads credentials
  mcp_server.auth = _build_auth_provider()
  print("mcp server starting...")
  app = _build_streamable_http_app()
  _serve_streamable_http_app(app)


if __name__ == "__main__":
  main()
