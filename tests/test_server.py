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

"""Tests for server.py."""

# pylint: disable=protected-access

import os
from unittest import mock

from ads_mcp import server
from fastmcp.server.auth.redirect_validation import DEFAULT_LOCALHOST_PATTERNS
from fastmcp.server.middleware.ping import PingMiddleware
import pytest


@mock.patch("builtins.print")
@mock.patch("ads_mcp.server._serve_streamable_http_app")
@mock.patch("ads_mcp.server._build_streamable_http_app", return_value="app")
@mock.patch(
    "ads_mcp.server._build_auth_provider", return_value="auth-provider"
)
@mock.patch("ads_mcp.server.api")
@mock.patch("ads_mcp.server.mcp_server")
@mock.patch("ads_mcp.server.update_views_yaml", new_callable=mock.Mock)
def test_main(
    mock_update_views,
    mock_mcp_server,
    mock_api,
    mock_build_auth_provider,
    mock_build_app,
    mock_serve_app,
    mock_print,
):
  """Tests main function."""
  with mock.patch("ads_mcp.server.asyncio.run"):
    server.main()

  mock_update_views.assert_called_once()
  mock_api.get_ads_client.assert_called_once()
  mock_build_auth_provider.assert_called_once_with()
  assert mock_mcp_server.auth == "auth-provider"
  mock_build_app.assert_called_once_with()
  mock_serve_app.assert_called_once_with("app")
  mock_print.assert_called_once_with("mcp server starting...")


@mock.patch.dict(
    os.environ,
    {
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID": "client-id",
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET": "client-secret",
    },
    clear=True,
)
@mock.patch("ads_mcp.server.GoogleProvider", return_value="provider")
def test_build_auth_provider_uses_localhost_redirect_allowlist(
    mock_google_provider,
):
  """Defaults to localhost redirect URI patterns for local OAuth setup."""
  assert server._build_auth_provider() == "provider"

  mock_google_provider.assert_called_once()
  provider_kwargs = mock_google_provider.call_args.kwargs
  assert provider_kwargs["allowed_client_redirect_uris"] == list(
      DEFAULT_LOCALHOST_PATTERNS
  )
  assert provider_kwargs["forward_resource"] is True


@mock.patch.dict(
    os.environ,
    {
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID": "client-id",
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET": "client-secret",
        "FASTMCP_SERVER_BASE_URL": "https://ads.example.com",
        "FASTMCP_SERVER_AUTH_ALLOWED_CLIENT_REDIRECT_URIS": (
            "https://app.example.com/callback, https://app.example.com/auth/*"
        ),
    },
    clear=True,
)
@mock.patch("ads_mcp.server.GoogleProvider", return_value="provider")
def test_build_auth_provider_uses_explicit_redirect_allowlist(
    mock_google_provider,
):
  """Uses explicit redirect URI allowlists for non-local deployments."""
  assert server._build_auth_provider() == "provider"

  provider_kwargs = mock_google_provider.call_args.kwargs
  assert provider_kwargs["allowed_client_redirect_uris"] == [
      "https://app.example.com/callback",
      "https://app.example.com/auth/*",
  ]


@mock.patch.dict(
    os.environ,
    {
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID": "client-id",
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET": "client-secret",
        "FASTMCP_SERVER_BASE_URL": "https://ads.example.com",
    },
    clear=True,
)
def test_build_auth_provider_requires_allowlist_for_remote_base_url():
  """Rejects remote OAuth setups without an explicit redirect allowlist."""
  with pytest.raises(
      ValueError,
      match="non-empty comma-separated list",
  ):
    server._build_auth_provider()


@mock.patch.dict(
    os.environ,
    {"USE_GOOGLE_OAUTH_ACCESS_TOKEN": "true"},
    clear=True,
)
@mock.patch(
    "ads_mcp.server.GoogleTokenVerifier", return_value="token-verifier"
)
def test_build_auth_provider_uses_google_token_verifier(
    mock_token_verifier,
):
  """Uses GoogleTokenVerifier when access-token verification is enabled."""
  assert server._build_auth_provider() == "token-verifier"
  mock_token_verifier.assert_called_once_with()


@mock.patch.dict(
    os.environ,
    {
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID": "client-id",
        "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET": "client-secret",
        "FASTMCP_SERVER_BASE_URL": "https://ads.example.com",
        "FASTMCP_SERVER_AUTH_ALLOWED_CLIENT_REDIRECT_URIS": " , ",
    },
    clear=True,
)
def test_build_auth_provider_rejects_empty_explicit_allowlist():
  """Rejects explicitly empty redirect allowlists for remote deployments."""
  with pytest.raises(
      ValueError,
      match="non-empty comma-separated list",
  ):
    server._build_auth_provider()


@mock.patch("ads_mcp.server.mcp_server")
@mock.patch("ads_mcp.server.EventStore", return_value="event-store")
@mock.patch("ads_mcp.server._ensure_ping_middleware")
@mock.patch.dict(
    os.environ,
    {"FASTMCP_STREAMABLE_HTTP_RETRY_INTERVAL_MS": "2500"},
    clear=True,
)
def test_build_streamable_http_app_uses_resumable_http_defaults(
    mock_ensure_ping_middleware,
    mock_event_store,
    mock_mcp_server,
):
  """Builds streamable-http apps with EventStore-backed resumability."""
  mock_mcp_server.http_app.return_value = "app"

  assert server._build_streamable_http_app() == "app"

  mock_ensure_ping_middleware.assert_called_once_with()
  mock_event_store.assert_called_once_with()
  mock_mcp_server.http_app.assert_called_once_with(
      transport="streamable-http",
      event_store="event-store",
      retry_interval=2500,
  )


@mock.patch("ads_mcp.server.mcp_server")
def test_ensure_ping_middleware_dedupes_existing_instance(mock_mcp_server):
  """Reuses the existing PingMiddleware instead of adding duplicates."""
  existing = PingMiddleware(interval_ms=1000)
  mock_mcp_server.middleware = [existing]

  with mock.patch.dict(
      os.environ, {"FASTMCP_SERVER_PING_INTERVAL_MS": "45000"}, clear=True
  ):
    server._ensure_ping_middleware()

  assert existing.interval_ms == 45000
  mock_mcp_server.add_middleware.assert_not_called()


def test_ensure_ping_middleware_rejects_invalid_interval():
  """Rejects invalid ping interval env values with a clear message."""
  with mock.patch.dict(
      os.environ, {"FASTMCP_SERVER_PING_INTERVAL_MS": "abc"}, clear=True
  ):
    with pytest.raises(ValueError, match="FASTMCP_SERVER_PING_INTERVAL_MS"):
      server._ensure_ping_middleware()


def test_get_retry_interval_ms_rejects_invalid_interval():
  """Rejects invalid retry interval env values with a clear message."""
  with mock.patch.dict(
      os.environ,
      {"FASTMCP_STREAMABLE_HTTP_RETRY_INTERVAL_MS": "abc"},
      clear=True,
  ):
    with pytest.raises(
        ValueError, match="FASTMCP_STREAMABLE_HTTP_RETRY_INTERVAL_MS"
    ):
      server._get_retry_interval_ms()


@mock.patch("ads_mcp.server.uvicorn.Server")
@mock.patch("ads_mcp.server.uvicorn.Config")
def test_serve_streamable_http_app(mock_uvicorn_config, mock_uvicorn_server):
  """Serves the streamable-http app with uvicorn defaults."""
  config = mock_uvicorn_config.return_value

  server._serve_streamable_http_app("app")

  mock_uvicorn_config.assert_called_once()
  assert mock_uvicorn_config.call_args.args[0] == "app"
  mock_uvicorn_server.assert_called_once_with(config)
  mock_uvicorn_server.return_value.run.assert_called_once_with()
