"""Video upload protocol, mutation uncertainty, and processing regressions."""

import io
import json
from types import SimpleNamespace
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.api_core.exceptions import ServiceUnavailable
from google.auth.exceptions import RefreshError
import pytest
import requests

from ads_mcp import server
from ads_mcp.tools import videos

CUSTOMER_ID = "1234567890"
UPLOAD_ID = "456"
RESOURCE_NAME = f"customers/{CUSTOMER_ID}/youTubeVideoUploads/{UPLOAD_ID}"
SESSION_URL = (
    "https://googleads.googleapis.com/resumable/upload/v25/customers/"
    f"{CUSTOMER_ID}/youTubeVideoUploads:create?upload_id=secret-session"
)


def _response(status=200, body=None, headers=None):
  response = requests.Response()
  response.status_code = status
  content = (
      json.dumps(body).encode() if not isinstance(body, str) else body.encode()
  )
  response.raw = io.BytesIO(content)
  response.encoding = "utf-8"
  response.headers.update(headers or {})
  return response


def _start_response(**headers):
  return _response(headers={"X-Goog-Upload-URL": SESSION_URL, **headers})


def _final_response(resource_name=RESOURCE_NAME):
  return _response(
      body={"resourceName": resource_name},
      headers={"X-Goog-Upload-Status": "final"},
  )


def _row(state="UPLOADED", video_id=""):
  return {
      "you_tube_video_upload.resource_name": RESOURCE_NAME,
      "you_tube_video_upload.video_upload_id": UPLOAD_ID,
      "you_tube_video_upload.state": state,
      "you_tube_video_upload.video_id": video_id,
      "you_tube_video_upload.video_privacy": "UNLISTED",
      "you_tube_video_upload.channel_id": "UC" + "a" * 22,
  }


@pytest.fixture(name="upload_env")
def _upload_env(tmp_path):
  path = tmp_path / "sample.mp4"
  path.write_bytes(b"video-bytes")
  credentials = SimpleNamespace(
      token="secret-access",
      refresh_token="secret-refresh",
      client_secret="secret-client",
  )
  client = SimpleNamespace(
      credentials=credentials,
      developer_token="secret-developer",
      login_customer_id="9990001112",
      linked_customer_id="8880001112",
  )
  with (
      mock.patch.object(
          videos, "get_ads_client", return_value=client
      ) as get_client,
      mock.patch.object(videos, "AuthorizedSession") as session_factory,
      mock.patch.object(
          videos, "run_gaql_query", return_value=[_row()]
      ) as query,
      mock.patch.object(videos.time, "sleep") as sleep,
  ):
    session = session_factory.return_value.__enter__.return_value
    session.post.side_effect = [_start_response(), _final_response()]
    yield SimpleNamespace(
        path=path,
        client=client,
        get_client=get_client,
        session_factory=session_factory,
        session=session,
        query=query,
        sleep=sleep,
    )


def _upload(env, **kwargs):
  return videos.upload_youtube_video(
      CUSTOMER_ID, str(env.path), "Video title", **kwargs
  )


def test_upload_uses_typed_native_metadata_scope_and_single_final_chunk(
    upload_env,
):
  result = _upload(upload_env, description="Product video")

  upload_env.get_client.assert_called_once_with(None)
  factory_args = upload_env.session_factory.call_args
  assert factory_args.args == (upload_env.client.credentials,)
  assert factory_args.kwargs["max_refresh_attempts"] == 0
  auth_session = factory_args.kwargs["auth_request"].session
  assert auth_session.get_adapter("https://").max_retries.total == 0
  assert upload_env.session.post.call_count == 2
  start, final = upload_env.session.post.call_args_list
  assert start.args == (
      f"https://googleads.googleapis.com/resumable/upload/v25/customers/"
      f"{CUSTOMER_ID}/youTubeVideoUploads:create",
  )
  assert json.loads(start.kwargs["data"]) == {
      "customer_id": CUSTOMER_ID,
      "you_tube_video_upload": {
          "video_title": "Video title",
          "video_description": "Product video",
          "video_privacy": "UNLISTED",
      },
  }
  assert start.kwargs["headers"]["X-Goog-Upload-Command"] == "start"
  assert start.kwargs["headers"]["X-Goog-Upload-Protocol"] == "resumable"
  assert start.kwargs["headers"]["X-Goog-Upload-Header-Content-Length"] == "11"
  for call in (start, final):
    assert call.kwargs["headers"]["developer-token"] == "secret-developer"
    assert call.kwargs["headers"]["login-customer-id"] == "9990001112"
    assert call.kwargs["headers"]["linked-customer-id"] == "8880001112"
    assert call.kwargs["allow_redirects"] is False
    assert 0 < call.kwargs["timeout"] <= 60
  assert final.args == (SESSION_URL,)
  assert final.kwargs["data"] == b"video-bytes"
  assert final.kwargs["headers"]["X-Goog-Upload-Command"] == "upload, finalize"
  assert final.kwargs["headers"]["X-Goog-Upload-Offset"] == "0"
  assert result["resource_name"] == RESOURCE_NAME
  assert result["video_upload_id"] == UPLOAD_ID
  assert result["uploaded_bytes_confirmed"] == 11
  assert result["upload_complete"] is True
  assert result["outcome_unknown"] is False
  assert result["state"] == "UPLOADED"
  assert result["ready"] is False
  assert result["status_call"] == {
      "tool": "get_youtube_video_upload",
      "arguments": {
          "customer_id": CUSTOMER_ID,
          "video_upload_id": UPLOAD_ID,
          "login_customer_id": None,
      },
  }
  assert "secret" not in json.dumps(result)
  upload_env.sleep.assert_not_called()


def test_normalized_explicit_account_and_public_brand_channel(upload_env):
  upload_env.client.login_customer_id = "9990001112"
  result = videos.upload_youtube_video(
      "123-456-7890",
      str(upload_env.path),
      "Video title",
      privacy="PUBLIC",
      channel_id="UC" + "b" * 22,
      login_customer_id="999-000-1112",
  )
  upload_env.get_client.assert_called_once_with("9990001112")
  body = json.loads(upload_env.session.post.call_args_list[0].kwargs["data"])
  assert body["customer_id"] == CUSTOMER_ID
  assert body["you_tube_video_upload"]["video_privacy"] == "PUBLIC"
  assert body["you_tube_video_upload"]["channel_id"] == "UC" + "b" * 22
  assert (
      result["status_call"]["arguments"]["login_customer_id"] == "9990001112"
  )
  assert upload_env.query.call_args.kwargs["login_customer_id"] == "9990001112"


def test_chunks_follow_granularity_offsets_and_send_each_byte_once(
    upload_env, monkeypatch
):
  monkeypatch.setattr(videos, "_CHUNK_BYTES", 4)
  upload_env.path.write_bytes(b"1234567890123")
  upload_env.session.post.side_effect = [
      _start_response(**{"X-Goog-Upload-Chunk-Granularity": "3"}),
      _response(headers={"X-Goog-Upload-Status": "active"}),
      _response(headers={"X-Goog-Upload-Status": "active"}),
      _final_response(),
  ]

  result = _upload(upload_env)

  calls = upload_env.session.post.call_args_list[1:]
  assert [call.kwargs["data"] for call in calls] == [
      b"123456",
      b"789012",
      b"3",
  ]
  assert [
      call.kwargs["headers"]["X-Goog-Upload-Offset"] for call in calls
  ] == ["0", "6", "12"]
  assert [
      call.kwargs["headers"]["X-Goog-Upload-Command"] for call in calls
  ] == ["upload", "upload", "upload, finalize"]
  assert result["uploaded_bytes_confirmed"] == 13
  assert result["upload_complete"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_id": "bad"},
        {"login_customer_id": "bad"},
        {"title": ""},
        {"title": " "},
        {"title": "x" * 101},
        {"title": "<invalid>"},
        {"title": True},
        {"description": "x" * 5001},
        {"description": "invalid>"},
        {"description": None},
        {"privacy": "PRIVATE"},
        {"privacy": 3},
        {"privacy": "PUBLIC"},
        {"channel_id": "UC-short"},
        {"channel_id": 3},
        {"wait": "true"},
        {"wait_timeout_seconds": 0},
        {"wait_timeout_seconds": 601},
        {"wait_timeout_seconds": True},
        {"poll_interval_seconds": 61},
        {"upload_timeout_seconds": 14401},
        {"upload_timeout_seconds": 0},
        {"file_path": "https://example.com/video.mp4"},
        {"file_path": ""},
        {"file_path": None},
    ],
)
def test_invalid_upload_arguments_are_rejected_before_clients(
    upload_env, overrides
):
  arguments = {
      "customer_id": CUSTOMER_ID,
      "file_path": str(upload_env.path),
      "title": "Video",
      **overrides,
  }
  with pytest.raises(ToolError):
    videos.upload_youtube_video(**arguments)
  upload_env.get_client.assert_not_called()
  upload_env.session_factory.assert_not_called()
  upload_env.query.assert_not_called()


@pytest.mark.parametrize("file_kind", ["empty", "missing", "directory"])
def test_file_validation_happens_before_clients(upload_env, file_kind):
  if file_kind == "empty":
    upload_env.path.write_bytes(b"")
  elif file_kind == "missing":
    upload_env.path.unlink()
  else:
    upload_env.path.unlink()
    upload_env.path.mkdir()
  with pytest.raises(ToolError, match="file"):
    _upload(upload_env)
  upload_env.get_client.assert_not_called()
  upload_env.session_factory.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://googleads.googleapis.com/resumable/upload/v25/x",
        "https://attacker.example/resumable/upload/v25/x",
        "https://googleads.googleapis.com.attacker.example/"
        "resumable/upload/v25/x",
        "https://user:password@googleads.googleapis.com/resumable/upload/v25/x",
        "https://@googleads.googleapis.com/resumable/upload/v25/x",
        "https://googleads.googleapis.com:8443/resumable/upload/v25/x",
        "https://googleads.googleapis.com/resumable/upload/v25/x#fragment",
        "https://googleads.googleapis.com/other/path",
        "https://googleads.googleapis.com:bad/resumable/upload/v25/x",
    ],
)
def test_untrusted_session_location_never_receives_credentials(
    upload_env, url
):
  upload_env.session.post.side_effect = [
      _response(headers={"X-Goog-Upload-URL": url})
  ]
  result = _upload(upload_env)
  assert result["upload_complete"] is False
  assert result["outcome_unknown"] is True
  assert "untrusted" in result["error"]
  assert upload_env.session.post.call_count == 1
  upload_env.query.assert_not_called()


@pytest.mark.parametrize(
    "granularity", ["0", "-1", "bad", "9999999999", "67108865"]
)
def test_invalid_chunk_granularity_stops_before_bytes_are_sent(
    upload_env, granularity
):
  upload_env.session.post.side_effect = [
      _start_response(**{"X-Goog-Upload-Chunk-Granularity": granularity})
  ]
  result = _upload(upload_env)
  assert result["upload_complete"] is False
  assert result["uploaded_bytes_confirmed"] == 0
  assert "granularity" in result["error"]
  assert upload_env.session.post.call_count == 1


@pytest.mark.parametrize("status", [302, 401, 429, 500, 503])
def test_http_error_body_is_bounded_redacted_and_never_retried(
    upload_env, status
):
  body = {
      "error": {
          "message": (
              "Permission denied secret-access secret-refresh secret-client "
              "secret-developer Bearer arbitrary-token "
              f"session={SESSION_URL} " + "x" * 4000
          ),
          "access_token": "another-access-token",
      }
  }
  upload_env.session.post.side_effect = [_response(status, body)]

  result = _upload(upload_env)

  assert result["http_status"] == status
  assert "Permission denied" in result["error"]
  assert len(result["error"]) <= 2048
  assert result["upload_complete"] is False
  assert result["outcome_unknown"] is True
  assert upload_env.session.post.call_count == 1
  for secret in (
      "secret-access",
      "secret-refresh",
      "secret-client",
      "secret-developer",
      "arbitrary-token",
      "another-access-token",
      "secret-session",
  ):
    assert secret not in json.dumps(result)


def test_error_field_redaction_without_truncation(upload_env):
  upload_env.session.post.side_effect = [
      _response(
          400,
          {
              "error": {
                  "client_secret": "unknown-secret",
                  "message": "Bad title",
              }
          },
      )
  ]
  result = _upload(upload_env)
  assert "Bad title" in result["error"]
  assert "unknown-secret" not in result["error"]


def test_token_refreshed_during_request_is_redacted_from_http_error(
    upload_env,
):
  def _after_refresh(*_args, **_kwargs):
    upload_env.client.credentials.token = "fresh-bare-credential-secret"
    return _response(
        403, {"error": {"message": "Denied fresh-bare-credential-secret"}}
    )

  upload_env.session.post.side_effect = _after_refresh
  result = _upload(upload_env)
  assert "Denied" in result["error"]
  assert "fresh-bare-credential-secret" not in json.dumps(result)
  assert upload_env.session.post.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", ["direct", "proxy"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"wait": "true"},
        {"wait": 1},
        {"wait": 0},
        {"wait_timeout_seconds": True},
        {"wait_timeout_seconds": "5"},
        {"wait_timeout_seconds": 5.0},
        {"poll_interval_seconds": "5"},
        {"upload_timeout_seconds": False},
    ],
)
async def test_mcp_rejects_coerced_scalars_before_clients(
    upload_env, dispatch, overrides
):
  arguments = {
      "customer_id": CUSTOMER_ID,
      "file_path": str(upload_env.path),
      "title": "Video",
      **overrides,
  }
  async with Client(server.mcp_server) as client:
    await client.call_tool("unlock_mutation_tools", {})
    with pytest.raises(ToolError):
      if dispatch == "direct":
        await client.call_tool("upload_youtube_video", arguments)
      else:
        await client.call_tool(
            "call_tool",
            {"name": "upload_youtube_video", "arguments": arguments},
        )
  upload_env.get_client.assert_not_called()
  upload_env.session_factory.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_upload_requires_session_unlock_and_keeps_status_read_visible(
    upload_env,
):
  arguments = {
      "customer_id": CUSTOMER_ID,
      "file_path": str(upload_env.path),
      "title": "Video",
  }
  async with Client(server.mcp_server) as client:
    visible = {tool.name for tool in await client.list_tools()}
    assert "upload_youtube_video" not in visible
    assert "get_youtube_video_upload" in visible
    with pytest.raises(ToolError):
      await client.call_tool("upload_youtube_video", arguments)
    upload_env.get_client.assert_not_called()
    await client.call_tool("unlock_mutation_tools", {})
    response = await client.call_tool("upload_youtube_video", arguments)
    assert response.structured_content["upload_complete"] is True
    assert response.structured_content["video_upload_id"] == UPLOAD_ID
    upload_env.get_client.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        requests.Timeout(SESSION_URL),
        requests.ConnectionError("Bearer leaked-token"),
        RefreshError("secret-refresh"),
        ServiceUnavailable("secret-developer"),
    ],
)
def test_network_or_auth_failure_never_replays_initiation(upload_env, error):
  upload_env.session.post.side_effect = error
  result = _upload(upload_env)
  assert result["upload_complete"] is False
  assert result["outcome_unknown"] is True
  assert result["uploaded_bytes_confirmed"] == 0
  assert upload_env.session.post.call_count == 1
  assert "secret" not in json.dumps(result)
  assert "leaked-token" not in json.dumps(result)


def test_uncertain_final_chunk_never_retries_or_polls(upload_env, monkeypatch):
  monkeypatch.setattr(videos, "_CHUNK_BYTES", 6)
  upload_env.session.post.side_effect = [
      _start_response(),
      _response(headers={"X-Goog-Upload-Status": "active"}),
      requests.Timeout(SESSION_URL),
  ]
  result = _upload(upload_env)
  assert result["upload_complete"] is False
  assert result["outcome_unknown"] is True
  assert result["uploaded_bytes_confirmed"] == 6
  assert result["video_upload_id"] is None
  assert "before uploading again" in result["recovery_hint"]
  assert "secret-session" not in json.dumps(result)
  assert upload_env.session.post.call_count == 3
  upload_env.query.assert_not_called()


def test_chunk_http_failure_reports_confirmed_progress_without_recovery(
    upload_env, monkeypatch
):
  monkeypatch.setattr(videos, "_CHUNK_BYTES", 6)
  upload_env.session.post.side_effect = [
      _start_response(),
      _response(headers={"X-Goog-Upload-Status": "active"}),
      _response(503, {"error": {"message": "Backend unavailable"}}),
  ]
  result = _upload(upload_env)
  assert result["uploaded_bytes_confirmed"] == 6
  assert result["http_status"] == 503
  assert "Backend unavailable" in result["error"]
  assert upload_env.session.post.call_count == 3


@pytest.mark.parametrize(
    "final_response",
    [
        _response(headers={"X-Goog-Upload-Status": "active"}),
        _response(),
        _response(body="not json", headers={"X-Goog-Upload-Status": "final"}),
        _final_response("customers/99/youTubeVideoUploads/456"),
        _final_response("invalid"),
        _final_response(
            f"customers/{CUSTOMER_ID}/youTubeVideoUploads/9223372036854775808"
        ),
    ],
)
def test_bad_final_response_preserves_uncertain_outcome_without_repeat(
    upload_env, final_response
):
  upload_env.session.post.side_effect = [_start_response(), final_response]
  result = _upload(upload_env)
  assert result["upload_complete"] is False
  assert result["outcome_unknown"] is True
  assert result["error"]
  assert upload_env.session.post.call_count == 2
  upload_env.query.assert_not_called()


def test_upload_deadline_stops_before_request(upload_env):
  with mock.patch.object(videos.time, "monotonic", side_effect=[0, 2]):
    result = _upload(upload_env, upload_timeout_seconds=1)
  assert "deadline" in result["error"]
  assert result["outcome_unknown"] is False
  assert result["recovery_hint"] == "No upload request was sent."
  upload_env.session.post.assert_not_called()


def test_optional_wait_reaches_processed_with_reusable_video_id(upload_env):
  upload_env.query.side_effect = [[_row()], [_row("PROCESSED", "abcdefghijk")]]
  result = _upload(upload_env, wait=True)
  assert result["state"] == "PROCESSED"
  assert result["video_id"] == "abcdefghijk"
  assert result["ready"] is True
  assert result["processing_complete"] is True
  assert result["wait_timed_out"] is False
  assert upload_env.query.call_count == 2
  upload_env.sleep.assert_called_once_with(5)
  assert upload_env.session.post.call_count == 2


@pytest.mark.parametrize("state", ["FAILED", "REJECTED", "UNAVAILABLE"])
def test_processing_failure_preserves_completed_upload_and_status_lookup(
    upload_env, state
):
  upload_env.query.return_value = [_row(state)]
  result = _upload(upload_env, wait=True)
  assert result["state"] == state
  assert result["upload_complete"] is True
  assert result["processing_complete"] is True
  assert result["ready"] is False
  assert result["resource_name"] == RESOURCE_NAME
  assert result["status_call"]["arguments"]["video_upload_id"] == UPLOAD_ID
  assert upload_env.session.post.call_count == 2
  upload_env.sleep.assert_not_called()


def test_poll_timeout_retains_upload_and_last_observed_state(upload_env):
  with mock.patch.object(
      videos.time, "monotonic", side_effect=[0, 0, 0, 0, 2]
  ):
    result = _upload(upload_env, wait=True, wait_timeout_seconds=1)
  assert result["wait_timed_out"] is True
  assert result["state"] == "UPLOADED"
  assert result["video_upload_id"] == UPLOAD_ID
  assert result["upload_complete"] is True
  assert result["outcome_unknown"] is False
  assert result["ready"] is False
  assert upload_env.session.post.call_count == 2


def test_polling_never_starts_another_read_after_sleep_exhausts_budget(
    upload_env,
):
  now = [0.0]

  def _advance(seconds):
    now[0] += seconds

  upload_env.sleep.side_effect = _advance
  with mock.patch.object(videos.time, "monotonic", side_effect=lambda: now[0]):
    result = _upload(upload_env, wait=True, wait_timeout_seconds=1)
  assert result["wait_timed_out"] is True
  assert result["state"] == "UPLOADED"
  assert result["upload_complete"] is True
  upload_env.query.assert_called_once()
  upload_env.sleep.assert_called_once_with(1)


def test_status_read_error_never_hides_successful_upload_or_invites_repeat(
    upload_env,
):
  upload_env.query.side_effect = ToolError(
      "Status unavailable secret-access " + SESSION_URL
  )
  result = _upload(upload_env, wait=True)
  assert result["upload_complete"] is True
  assert result["resource_name"] == RESOURCE_NAME
  assert result["video_upload_id"] == UPLOAD_ID
  assert result["outcome_unknown"] is False
  assert "Status unavailable" in result["processing_status_error"]
  assert "secret" not in json.dumps(result)
  assert upload_env.session.post.call_count == 2
  upload_env.sleep.assert_not_called()


def test_get_upload_uses_only_selectable_native_fields_and_scoped_resource(
    upload_env,
):
  upload_env.query.return_value = [_row("PROCESSED", "abcdefghijk")]
  result = videos.get_youtube_video_upload(
      "123-456-7890", "000456", login_customer_id="999-000-1112"
  )
  upload_env.get_client.assert_not_called()
  upload_env.session_factory.assert_not_called()
  assert result["ready"] is True
  assert result["video_id"] == "abcdefghijk"
  arguments = upload_env.query.call_args.kwargs
  assert arguments["customer_id"] == CUSTOMER_ID
  assert arguments["login_customer_id"] == "9990001112"
  assert RESOURCE_NAME in arguments["query"]
  assert "video_title" not in arguments["query"]
  assert "video_description" not in arguments["query"]
  assert "LIMIT 1" in arguments["query"]


def test_missing_upload_is_explicit_and_does_not_synthesize_pending_state(
    upload_env,
):
  upload_env.query.return_value = []
  result = videos.get_youtube_video_upload(CUSTOMER_ID, UPLOAD_ID)
  assert result["found"] is False
  assert result["state"] == "UNKNOWN"
  assert result["video_id"] is None
  assert result["ready"] is False
  assert result["processing_complete"] is False


@pytest.mark.parametrize(
    "upload_id",
    ["", "-1", "0", "1 OR 1=1", "9" * 20, "9223372036854775808", True, 4],
)
def test_get_upload_rejects_invalid_id_before_query(upload_env, upload_id):
  with pytest.raises(ToolError):
    videos.get_youtube_video_upload(CUSTOMER_ID, upload_id)
  upload_env.query.assert_not_called()
