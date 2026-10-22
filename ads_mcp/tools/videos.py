"""Local-file YouTube uploads with no automatic mutation retries."""

import contextlib
import json
import os
from pathlib import Path
import re
import stat
import time
from typing import Any
from urllib.parse import urlsplit

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types.youtube_video_upload_service import (
    CreateYouTubeVideoUploadRequest,
    CreateYouTubeVideoUploadResponse,
)
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request
from google.protobuf.json_format import MessageToJson
from google.protobuf.json_format import ParseDict
from google.protobuf.json_format import ParseError
from pydantic import StrictBool
from pydantic import StrictInt
import requests

from ads_mcp.api_version import API_VERSION
from ads_mcp.coordinator import mcp_server as mcp
from ads_mcp.tooling import ads_mutation_tool
from ads_mcp.tooling import ads_read_tool
from ads_mcp.tools._service import normalized_account_id
from ads_mcp.tools._service import parse_service_request
from ads_mcp.tools.api import get_ads_client
from ads_mcp.tools.api import handle_google_ads_errors
from ads_mcp.tools.api import run_gaql_query

_UPLOAD_ORIGIN = "https://googleads.googleapis.com"
_CHUNK_BYTES = 10 * 1024 * 1024
_MAX_CHUNK_BYTES = 64 * 1024 * 1024
_ERROR_CHAR_LIMIT = 2048
_TERMINAL_STATES = {"PROCESSED", "FAILED", "REJECTED", "UNAVAILABLE"}
_RESOURCE_PATTERN = re.compile(
    r"customers/([0-9]+)/youTubeVideoUploads/([0-9]+)"
)


class _UploadFailure(Exception):
  """A bounded upload failure with confirmed progress and uncertainty."""

  def __init__(
      self,
      message: str,
      http_status: int | None = None,
      *,
      outcome_unknown: bool = True,
  ):
    super().__init__(message)
    self.http_status = http_status
    self.outcome_unknown = outcome_unknown


def _bounded_int(value: int, name: str, maximum: int) -> None:
  if isinstance(value, bool) or not isinstance(value, int):
    raise ToolError(f"{name} must be an integer.")
  if not 1 <= value <= maximum:
    raise ToolError(f"{name} must be between 1 and {maximum}.")


def _upload_id(value: str) -> str:
  if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
    raise ToolError("video_upload_id must be a positive int64 ID string.")
  numeric = int(value)
  if not 0 < numeric < 2**63:
    raise ToolError("video_upload_id must be a positive int64 ID string.")
  return str(numeric)


def _account_scope(
    customer_id: str, login_customer_id: str | None
) -> tuple[str, str | None]:
  customer_id = normalized_account_id(customer_id)
  if login_customer_id is not None:
    login_customer_id = normalized_account_id(
        login_customer_id, "login_customer_id"
    )
  return customer_id, login_customer_id


def _metadata_request(
    customer_id: str,
    title: str,
    description: str,
    privacy: str,
    channel_id: str | None,
) -> CreateYouTubeVideoUploadRequest:
  for name, value, maximum in (
      ("title", title, 100),
      ("description", description, 5000),
  ):
    if not isinstance(value, str) or len(value) > maximum:
      raise ToolError(
          f"{name} must be a string of at most {maximum} characters."
      )
    if "<" in value or ">" in value:
      raise ToolError(f"{name} must not contain angle brackets.")
  if not title.strip():
    raise ToolError("title must not be empty.")
  if not isinstance(privacy, str) or privacy not in {"UNLISTED", "PUBLIC"}:
    raise ToolError("privacy must be UNLISTED or PUBLIC.")
  if channel_id is not None and (
      not isinstance(channel_id, str)
      or not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id)
  ):
    raise ToolError("channel_id must be a 24-character YouTube UC channel ID.")
  if privacy == "PUBLIC" and channel_id is None:
    raise ToolError("PUBLIC privacy requires an advertiser-owned channel_id.")
  metadata = {
      "video_title": title,
      "video_description": description,
      "video_privacy": privacy,
  }
  if channel_id is not None:
    metadata["channel_id"] = channel_id
  return parse_service_request(
      {"customer_id": customer_id, "you_tube_video_upload": metadata},
      CreateYouTubeVideoUploadRequest,
  )


@contextlib.contextmanager
def _video_stream(file_path: str):
  """Opens and validates a readable regular file before any client call."""
  if (
      not isinstance(file_path, str)
      or not file_path.strip()
      or "://" in file_path
  ):
    raise ToolError("file_path must name a readable nonempty local file.")
  try:
    # Nonblocking open prevents a FIFO/device path from hanging validation.
    descriptor = os.open(
        Path(file_path).expanduser(),
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
    )
    stream = os.fdopen(descriptor, "rb")
  except (OSError, ValueError) as exc:
    raise ToolError(
        "file_path must name a readable nonempty local file."
    ) from exc
  with stream:
    details = os.fstat(stream.fileno())
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0:
      raise ToolError("file_path must name a readable nonempty regular file.")
    try:
      if not stream.read(1):
        raise ToolError("file_path must name a readable nonempty local file.")
      stream.seek(0)
    except OSError as exc:
      raise ToolError("file_path must name a readable local file.") from exc
    yield stream, details.st_size


def _safe_error(message: str, ads_client: Any) -> str:
  credentials = ads_client.credentials
  secrets = (
      ads_client.developer_token,
      getattr(credentials, "token", None),
      getattr(credentials, "refresh_token", None),
      getattr(credentials, "client_secret", None),
  )
  for secret in secrets:
    if isinstance(secret, str) and secret:
      message = message.replace(secret, "[REDACTED]")
  message = re.sub(r"https?://[^\s\"'<>]+", "[REDACTED_URL]", message)
  message = re.sub(r"(?i)\bBearer\s+[^\s\"',}]+", "Bearer [REDACTED]", message)
  message = re.sub(
      r"(?i)([\"']?(?:access_token|refresh_token|client_secret|"
      r"developer[-_]token|authorization|upload_id|upload_url|session_url)"
      r"[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^,}\s]+)",
      lambda match: match[1] + json.dumps("[REDACTED]"),
      message,
  )
  return message[:_ERROR_CHAR_LIMIT]


def _post(
    session: AuthorizedSession,
    url: str,
    data: str | bytes,
    headers: dict[str, str],
    deadline: float,
    ads_client: Any,
) -> requests.Response:
  remaining = deadline - time.monotonic()
  if remaining <= 0:
    raise _UploadFailure("Upload deadline exceeded.", outcome_unknown=False)
  try:
    with handle_google_ads_errors():
      response = session.post(
          url,
          data=data,
          headers=headers,
          timeout=min(remaining, 60),
          allow_redirects=False,
      )
  except (requests.RequestException, GoogleAuthError, ToolError) as exc:
    # Exception text can contain the secret resumable URL or auth headers.
    raise _UploadFailure(
        f"Upload request failed ({type(exc).__name__})."
    ) from exc
  if not 200 <= response.status_code < 300:
    body = _safe_error(response.text, ads_client) or "(empty response body)"
    raise _UploadFailure(
        f"HTTP {response.status_code}: {body}",
        response.status_code,
    )
  return response


def _session_url(response: requests.Response) -> tuple[str, int]:
  url = response.headers.get("X-Goog-Upload-URL", "")
  try:
    parsed = urlsplit(url)
    valid = (
        parsed.scheme == "https"
        and parsed.hostname == "googleads.googleapis.com"
        and parsed.port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and parsed.path.startswith("/resumable/upload/")
    )
  except ValueError:
    valid = False
  if not valid:
    raise _UploadFailure("Missing or untrusted Google resumable upload URL.")
  granularity = response.headers.get("X-Goog-Upload-Chunk-Granularity", "1")
  if not re.fullmatch(r"[0-9]{1,9}", granularity) or not (
      0 < int(granularity) <= _MAX_CHUNK_BYTES
  ):
    raise _UploadFailure("Invalid or unsupported upload chunk granularity.")
  granularity = int(granularity)
  chunk_size = ((_CHUNK_BYTES + granularity - 1) // granularity) * granularity
  if chunk_size > _MAX_CHUNK_BYTES:
    raise _UploadFailure("Upload chunk granularity exceeds the memory bound.")
  return url, chunk_size


def _read_upload(
    customer_id: str, video_upload_id: str, login_customer_id: str | None
) -> dict[str, Any]:
  resource_name = (
      f"customers/{customer_id}/youTubeVideoUploads/{video_upload_id}"
  )
  query = f"""
      SELECT
        you_tube_video_upload.resource_name,
        you_tube_video_upload.video_upload_id,
        you_tube_video_upload.state,
        you_tube_video_upload.video_id,
        you_tube_video_upload.channel_id,
        you_tube_video_upload.video_privacy
      FROM you_tube_video_upload
      WHERE you_tube_video_upload.resource_name = '{resource_name}'
      LIMIT 1
  """
  with handle_google_ads_errors():
    rows = run_gaql_query(
        query=query,
        customer_id=customer_id,
        login_customer_id=login_customer_id,
    )
  result = {
      "customer_id": customer_id,
      "resource_name": resource_name,
      "video_upload_id": video_upload_id,
      "state": "UNKNOWN",
      "video_id": None,
      "found": bool(rows),
      "ready": False,
      "processing_complete": False,
  }
  if rows:
    row = rows[0]
    for field in ("state", "video_id", "channel_id", "video_privacy"):
      value = row.get(f"you_tube_video_upload.{field}")
      if value is not None:
        result[field] = value
    result["processing_complete"] = result["state"] in _TERMINAL_STATES
    result["ready"] = result["state"] == "PROCESSED" and bool(
        result["video_id"]
    )
  return result


@ads_read_tool(mcp, tags={"videos", "assets"})
def get_youtube_video_upload(
    customer_id: str,
    video_upload_id: str,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Reads processing state and the usable YouTube ID for one video upload.

  Args:
      customer_id: Google Ads customer ID that owns the upload.
      video_upload_id: Numeric upload ID returned by upload_youtube_video.
      login_customer_id: Optional manager account ID.

  Returns:
      The observed upload state, video_id, and ready flag. A missing row has
      found=false and state=UNKNOWN; only PROCESSED with a video ID is ready.
  """
  customer_id, login_customer_id = _account_scope(
      customer_id, login_customer_id
  )
  return _read_upload(
      customer_id, _upload_id(video_upload_id), login_customer_id
  )


def _wait_for_processing(
    result: dict[str, Any],
    login_customer_id: str | None,
    wait: bool,
    wait_timeout_seconds: int,
    poll_interval_seconds: int,
    ads_client: Any,
) -> dict[str, Any]:
  deadline = time.monotonic() + wait_timeout_seconds
  while True:
    try:
      result.update(
          _read_upload(
              result["customer_id"],
              result["video_upload_id"],
              login_customer_id,
          )
      )
    except ToolError as exc:
      result["processing_status_error"] = _safe_error(str(exc), ads_client)
      return result
    if result["processing_complete"] or not wait:
      return result
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      result["wait_timed_out"] = True
      return result
    time.sleep(min(poll_interval_seconds, remaining))
    if time.monotonic() >= deadline:
      result["wait_timed_out"] = True
      return result


@ads_mutation_tool(mcp, tags={"videos", "assets"})
def upload_youtube_video(
    customer_id: str,
    file_path: str,
    title: str,
    description: str = "",
    privacy: str = "UNLISTED",
    channel_id: str | None = None,
    wait: StrictBool = False,
    wait_timeout_seconds: StrictInt = 120,
    poll_interval_seconds: StrictInt = 5,
    upload_timeout_seconds: StrictInt = 600,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
  """Uploads one local video to YouTube for Google Ads without retrying writes.

  Requires mutation tools to be unlocked. Uploads to the Google-managed
  channel by default. An advertiser-owned channel requires user OAuth.
  This service has no validate-only mode. URL downloads are unsupported.
  After PROCESSED, use video_id as asset.youtube_video_asset.youtube_video_id
  in mutate_ads_resources. This tool does not create an asset or ad.

  Args:
      customer_id: Google Ads customer ID that will own the upload.
      file_path: Readable nonempty regular video file on the MCP server.
      title: Required video title, at most 100 characters, without < or >.
      description: Optional description, at most 5000 characters, without
          angle brackets.
      privacy: UNLISTED, or PUBLIC with an advertiser-owned channel_id.
      channel_id: Optional 24-character UC channel ID. Omitted uploads use
          the Google-managed channel.
      wait: Whether to poll until processing completes or the wait expires.
      wait_timeout_seconds: Processing polling budget, 1-600 seconds. An
          in-flight GAQL read can outlast this budget.
      poll_interval_seconds: Seconds between processing reads, 1-60.
      upload_timeout_seconds: Upload deadline, 1-14400 seconds; each HTTP
          request also has a 60-second network timeout.
      login_customer_id: Optional manager account ID.

  Returns:
      Upload resource, video_upload_id, observed state, video_id and ready.
      upload_complete confirms byte upload, not YouTube processing. A timeout
      or status-read error preserves the ID for get_youtube_video_upload.
      Failures report confirmed bytes and outcome_unknown; inspect existing
      uploads before repeating an uncertain write. Session URLs and auth
      secrets are never returned. HTTP error bodies are redacted and bounded.
  """
  customer_id, login_customer_id = _account_scope(
      customer_id, login_customer_id
  )
  if not isinstance(wait, bool):
    raise ToolError("wait must be a boolean.")
  _bounded_int(wait_timeout_seconds, "wait_timeout_seconds", 600)
  _bounded_int(poll_interval_seconds, "poll_interval_seconds", 60)
  _bounded_int(upload_timeout_seconds, "upload_timeout_seconds", 14400)
  request = _metadata_request(
      customer_id, title, description, privacy, channel_id
  )
  result = {
      "customer_id": customer_id,
      "resource_name": None,
      "video_upload_id": None,
      "video_id": None,
      "state": "UNKNOWN",
      "ready": False,
      "processing_complete": False,
      "upload_complete": False,
      "outcome_unknown": False,
      "uploaded_bytes_confirmed": 0,
      "wait_timed_out": False,
  }
  with _video_stream(file_path) as (stream, size):
    ads_client = get_ads_client(login_customer_id)
    credentials = ads_client.credentials
    headers = {}
    if ads_client.developer_token:
      headers["developer-token"] = ads_client.developer_token
    for header, value in (
        ("login-customer-id", ads_client.login_customer_id),
        ("linked-customer-id", ads_client.linked_customer_id),
    ):
      if value:
        headers[header] = normalized_account_id(value, header)
    deadline = time.monotonic() + upload_timeout_seconds
    # SDK 33's public helper retries chunks even when retry=None. Use its
    # typed metadata with the documented REST protocol instead, without any
    # SDK monkeypatch. Disable AuthorizedSession's automatic 401 replay too.
    with (
        requests.Session() as auth_session,
        AuthorizedSession(
            credentials,
            max_refresh_attempts=0,
            auth_request=Request(auth_session),
        ) as session,
    ):
      try:
        start_headers = {
            **headers,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": "application/octet-stream",
        }
        response = _post(
            session,
            f"{_UPLOAD_ORIGIN}/resumable/upload/{API_VERSION}/customers/"
            f"{customer_id}/youTubeVideoUploads:create",
            MessageToJson(
                CreateYouTubeVideoUploadRequest.pb(request),
                preserving_proto_field_name=True,
            ),
            start_headers,
            deadline,
            ads_client,
        )
        result["outcome_unknown"] = True
        url, chunk_size = _session_url(response)
        while result["uploaded_bytes_confirmed"] < size:
          offset = result["uploaded_bytes_confirmed"]
          expected = min(chunk_size, size - offset)
          data = stream.read(expected)
          if len(data) != expected:
            raise _UploadFailure("Local file changed or became unreadable.")
          final = offset + len(data) == size
          response = _post(
              session,
              url,
              data,
              {
                  **headers,
                  "Content-Type": "application/octet-stream",
                  "X-Goog-Upload-Command": "upload, finalize"
                  if final
                  else "upload",
                  "X-Goog-Upload-Offset": str(offset),
              },
              deadline,
              ads_client,
          )
          expected_status = "final" if final else "active"
          if response.headers.get("X-Goog-Upload-Status") != expected_status:
            raise _UploadFailure("Unexpected resumable upload status.")
          result["uploaded_bytes_confirmed"] += len(data)
        native_response = CreateYouTubeVideoUploadResponse()
        ParseDict(
            response.json(),
            CreateYouTubeVideoUploadResponse.pb(native_response),
            ignore_unknown_fields=True,
        )
        resource_name = native_response.resource_name
        match = _RESOURCE_PATTERN.fullmatch(resource_name)
        if not match or match[1] != customer_id:
          raise _UploadFailure("Invalid or mismatched upload resource name.")
        video_upload_id = _upload_id(match[2])
      except (
          _UploadFailure,
          OSError,
          ValueError,
          ParseError,
          ToolError,
      ) as exc:
        result["outcome_unknown"] = result["outcome_unknown"] or getattr(
            exc, "outcome_unknown", True
        )
        result["error"] = _safe_error(str(exc), ads_client)
        result["http_status"] = getattr(exc, "http_status", None)
        result["recovery_hint"] = (
            (
                "The upload was not confirmed. No write was retried. Inspect "
                "you_tube_video_upload with execute_gaql before uploading "
                "again; an interrupted request may have created or finalized "
                "an upload."
            )
            if result["outcome_unknown"]
            else "No upload request was sent."
        )
        return result
    result.update(
        resource_name=resource_name,
        video_upload_id=video_upload_id,
        upload_complete=True,
        outcome_unknown=False,
        status_call={
            "tool": "get_youtube_video_upload",
            "arguments": {
                "customer_id": customer_id,
                "video_upload_id": video_upload_id,
                "login_customer_id": login_customer_id,
            },
        },
    )
  return _wait_for_processing(
      result,
      login_customer_id,
      wait,
      wait_timeout_seconds,
      poll_interval_seconds,
      ads_client,
  )
