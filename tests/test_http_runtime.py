"""Offline HTTP regressions for the MCP session security/runtime update."""

# pylint: disable=protected-access

import asyncio
from contextlib import asynccontextmanager
import json
import time
from unittest import mock

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from fastmcp.server.http import FastMCPStreamableHTTPSessionManager
import httpx
import pytest

from ads_mcp import server


_SCOPE = "https://www.googleapis.com/auth/adwords"
_PROTOCOL_VERSION = "2025-06-18"
_INITIALIZE_PARAMS = {
    "protocolVersion": _PROTOCOL_VERSION,
    "capabilities": {},
    "clientInfo": {"name": "offline-regression", "version": "1"},
}


class _OfflineVerifier(TokenVerifier):
  """Returns deterministic validated tokens without credentials or network."""

  def __init__(self, tokens):
    super().__init__(required_scopes=[_SCOPE])
    self.tokens = tokens

  async def verify_token(self, token):
    return self.tokens.get(token)


def _token(value, *, client_id="client", subject="user", issuer="issuer"):
  return AccessToken(
      token=value,
      client_id=client_id,
      subject=subject,
      scopes=[_SCOPE],
      expires_at=int(time.time()) + 3600,
      claims={"iss": issuer},
  )


@asynccontextmanager
async def _http_runtime(verifier, **limits):
  """Runs the application's real FastMCP HTTP/auth wiring in memory."""
  managers = []

  def make_manager(*args, **kwargs):
    manager = FastMCPStreamableHTTPSessionManager(*args, **kwargs)
    for name, value in limits.items():
      setattr(manager, name, value)
    managers.append(manager)
    return manager

  with (
      mock.patch.object(server.mcp_server, "auth", verifier),
      mock.patch(
          "fastmcp.server.http.FastMCPStreamableHTTPSessionManager",
          side_effect=make_manager,
      ),
  ):
    app = server._build_streamable_http_app()
    async with asyncio.timeout(10), app.lifespan(app):
      async with httpx.AsyncClient(
          transport=httpx.ASGITransport(app=app),
          base_url="http://testserver",
      ) as client:
        yield client, managers[0]


def _headers(token, session_id=None):
  headers = {
      "Authorization": f"Bearer {token}",
      "Accept": "application/json, text/event-stream",
      "MCP-Protocol-Version": _PROTOCOL_VERSION,
  }
  if session_id:
    headers["MCP-Session-Id"] = session_id
  return headers


async def _rpc(client, token, session_id, method, params=None):
  return await client.post(
      "/mcp",
      headers=_headers(token, session_id),
      json={
          "jsonrpc": "2.0",
          "id": 1,
          "method": method,
          "params": params or {},
      },
  )


def _result(response):
  assert response.status_code == 200, response.text
  if response.headers["content-type"].startswith("application/json"):
    message = response.json()
  else:
    messages = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:]
    ]
    message = next(item for item in messages if item.get("id") == 1)
  assert "error" not in message, message
  return message["result"]


async def _initialize(client, token):
  response = await _rpc(
      client,
      token,
      None,
      "initialize",
      _INITIALIZE_PARAMS,
  )
  _result(response)
  session_id = response.headers["MCP-Session-Id"]
  initialized = await client.post(
      "/mcp",
      headers=_headers(token, session_id),
      json={"jsonrpc": "2.0", "method": "notifications/initialized"},
  )
  assert initialized.status_code == 202
  return session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("http_method", ["POST", "GET", "DELETE"])
@pytest.mark.parametrize(
    "identity_component", ["client_id", "subject", "issuer"]
)
async def test_http_session_rejects_another_principal(
    http_method, identity_component
):
  """Foreign POST/GET/DELETE cannot read or destroy an owner's session."""
  owner = _token("owner")
  foreign_options = {identity_component: "different"}
  if identity_component == "client_id":
    # GoogleTokenVerifier uses the Google user ID as client_id and leaves
    # typed subject unset. Cover that real provider's identity shape too.
    owner.subject = None
    foreign_options["subject"] = None
  stranger = _token("stranger", **foreign_options)
  async with _http_runtime(
      _OfflineVerifier({"owner": owner, "stranger": stranger})
  ) as (client, _):
    session_id = await _initialize(client, "owner")
    if http_method == "POST":
      refused = await _rpc(client, "stranger", session_id, "tools/list")
    else:
      refused = await client.request(
          http_method, "/mcp", headers=_headers("stranger", session_id)
      )
    assert refused.status_code == 404
    assert refused.json()["error"]["message"] == "Session not found"
    assert _result(await _rpc(client, "owner", session_id, "tools/list"))[
        "tools"
    ]


@pytest.mark.asyncio
async def test_http_refreshed_token_preserves_session_and_isolated_visibility():
  """Token rotation preserves local unlock state without unlocking others."""
  tokens = {
      "owner": _token("owner"),
      "rotated": _token("rotated"),
      "stranger": _token("stranger", subject="other-user"),
  }
  async with _http_runtime(_OfflineVerifier(tokens)) as (client, manager):
    assert manager.session_idle_timeout == 1800
    assert manager.max_sessions == 10000
    owner_session = await _initialize(client, "owner")
    stranger_session = await _initialize(client, "stranger")
    before = _result(await _rpc(client, "owner", owner_session, "tools/list"))
    assert "apply_recommendations" not in {
        tool["name"] for tool in before["tools"]
    }
    unlocked = _result(
        await _rpc(
            client,
            "owner",
            owner_session,
            "tools/call",
            {"name": "unlock_mutation_tools", "arguments": {}},
        )
    )
    assert unlocked["structuredContent"] == {"mutation_tools_unlocked": True}
    after = _result(await _rpc(client, "rotated", owner_session, "tools/list"))
    assert "apply_recommendations" in {tool["name"] for tool in after["tools"]}
    stranger_tools = _result(
        await _rpc(client, "stranger", stranger_session, "tools/list")
    )
    assert "apply_recommendations" not in {
        tool["name"] for tool in stranger_tools["tools"]
    }
    refused = await _rpc(client, "stranger", owner_session, "tools/list")
    assert refused.status_code == 404


@pytest.mark.asyncio
async def test_google_token_verifier_user_identity_survives_token_refresh():
  """Real Google verification, with mocked HTTP, binds transport to users."""
  users = {
      "first": "google-user-a",
      "refresh": "google-user-a",
      "other": "google-user-b",
  }

  def google_response(request):
    if request.url.path == "/tokeninfo":
      return httpx.Response(
          200,
          json={
              "aud": "same-google-oauth-app",
              "sub": users[request.url.params["access_token"]],
              "scope": _SCOPE,
              "expires_in": 3600,
          },
      )
    assert request.url.path == "/oauth2/v2/userinfo"
    return httpx.Response(200, json={"name": "Offline user"})

  async with httpx.AsyncClient(
      transport=httpx.MockTransport(google_response)
  ) as google_client:
    verifier = GoogleTokenVerifier(
        required_scopes=[_SCOPE], http_client=google_client
    )
    first = await verifier.verify_token("first")
    refresh = await verifier.verify_token("refresh")
    other = await verifier.verify_token("other")
    assert first.client_id == refresh.client_id == "google-user-a"
    assert other.client_id == "google-user-b"
    assert first.subject is None
    async with _http_runtime(verifier) as (client, _):
      session_id = await _initialize(client, "first")
      assert _result(await _rpc(client, "refresh", session_id, "tools/list"))[
          "tools"
      ]
      refused = await _rpc(client, "other", session_id, "tools/list")
      assert refused.status_code == 404


@pytest.mark.asyncio
async def test_http_session_limit_releases_slot_after_delete():
  """A full manager admits another session after its owner deletes one."""
  verifier = _OfflineVerifier({"owner": _token("owner")})
  async with _http_runtime(verifier, max_sessions=1) as (client, _):
    session_id = await _initialize(client, "owner")
    refused = await _rpc(
        client, "owner", None, "initialize", _INITIALIZE_PARAMS
    )
    assert refused.status_code == 503
    assert refused.json()["error"]["message"] == "Too many open sessions"
    deleted = await client.delete(
        "/mcp", headers=_headers("owner", session_id)
    )
    assert deleted.status_code == 200
    replacement = await _initialize(client, "owner")
    assert replacement != session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True])
async def test_http_body_limit_rejects_before_session_creation(chunked):
  """Declared and streamed oversized bodies cannot create new sessions."""
  verifier = _OfflineVerifier({"owner": _token("owner")})
  async with _http_runtime(verifier) as (client, manager):
    assert manager.max_request_body_size == 4 * 1024 * 1024
    oversized_body = b" " * (manager.max_request_body_size + 1)

    async def stream_body():
      yield oversized_body

    headers = _headers("owner")
    headers["Content-Type"] = "application/json"
    refused = await client.post(
        "/mcp",
        headers=headers,
        content=stream_body() if chunked else oversized_body,
    )
    assert refused.status_code == 413
    assert not manager._server_instances
    assert await _initialize(client, "owner")


@pytest.mark.asyncio
async def test_http_idle_expiry_requires_new_locked_session():
  """Expired IDs return 404; reinitialization does not retain an unlock."""
  verifier = _OfflineVerifier({"owner": _token("owner")})
  async with _http_runtime(verifier, session_idle_timeout=0.5) as (
      client,
      manager,
  ):
    session_id = await _initialize(client, "owner")
    _result(
        await _rpc(
            client,
            "owner",
            session_id,
            "tools/call",
            {"name": "unlock_mutation_tools", "arguments": {}},
        )
    )
    async with asyncio.timeout(3):
      while session_id in manager._server_instances:
        await asyncio.sleep(0.02)
    expired = await _rpc(client, "owner", session_id, "tools/list")
    assert expired.status_code == 404
    replacement = await _initialize(client, "owner")
    assert replacement != session_id
    tools = _result(await _rpc(client, "owner", replacement, "tools/list"))
    assert "apply_recommendations" not in {
        tool["name"] for tool in tools["tools"]
    }
