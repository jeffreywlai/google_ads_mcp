# Copyright 2026 Google LLC
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

"""Opt-in, payload-free stderr diagnostics for connection investigations."""

import hashlib
import importlib.metadata
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import uuid

from fastmcp.server.middleware import Middleware

_LOGGER = logging.getLogger("ads_mcp.diagnostics")


def _build_fingerprint() -> str:
  digest = hashlib.sha256()
  root = Path(__file__).parent
  for path in sorted(root.rglob("*.py")):
    digest.update(str(path.relative_to(root)).encode("utf-8"))
    digest.update(path.read_bytes())
  return digest.hexdigest()[:16]


class ToolDiagnostics(Middleware):
  """Records call timing and bytes without arguments, results, or error text."""

  async def on_call_tool(self, context: Any, call_next: Any) -> Any:
    name = getattr(context.message, "name", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z_]{1,80}", name):
      name = "unrecognized"
    call_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    _LOGGER.info("call_start id=%s tool=%s", call_id, name)
    try:
      result = await call_next(context)
    except BaseException as exc:
      _LOGGER.info(
          "call_failed id=%s tool=%s duration_ms=%.1f error_type=%s",
          call_id,
          name,
          (time.perf_counter() - started) * 1000,
          type(exc).__name__,
      )
      raise
    # ToolResult includes both text and structured content. This is the result
    # envelope size before transport framing, not a byte limit for source data.
    try:
      response_bytes = len(result.model_dump_json().encode("utf-8"))
    except (TypeError, ValueError):
      response_bytes = "unavailable"
    _LOGGER.info(
        "call_done id=%s tool=%s duration_ms=%.1f response_bytes=%s",
        call_id,
        name,
        (time.perf_counter() - started) * 1000,
        response_bytes,
    )
    return result


def enable_diagnostics(server: Any, transport: str) -> None:
  """Enables diagnostic stderr records when explicitly configured."""
  if os.getenv("GOOGLE_ADS_MCP_DIAGNOSTICS") != "1":
    return
  if not _LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _LOGGER.addHandler(handler)
  _LOGGER.propagate = False
  _LOGGER.setLevel(logging.INFO)
  server.add_middleware(ToolDiagnostics())
  _LOGGER.info(
      "server_start transport=%s package=%s sdk=%s fastmcp=%s build=%s",
      transport,
      importlib.metadata.version("google-ads-mcp"),
      importlib.metadata.version("google-ads"),
      importlib.metadata.version("fastmcp"),
      _build_fingerprint(),
  )
