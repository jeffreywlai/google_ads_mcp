"""Connection diagnostics never expose payloads or alter tool outcomes."""

import asyncio
from types import SimpleNamespace
from unittest import mock

from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult
import pytest

from ads_mcp import diagnostics


def test_diagnostics_are_opt_in(monkeypatch):
  monkeypatch.delenv("GOOGLE_ADS_MCP_DIAGNOSTICS", raising=False)
  server = mock.Mock()
  with mock.patch.object(diagnostics, "_LOGGER") as logger:
    diagnostics.enable_diagnostics(server, "stdio")
  server.add_middleware.assert_not_called()
  logger.info.assert_not_called()


def test_enabled_startup_records_build_without_payloads(monkeypatch):
  monkeypatch.setenv("GOOGLE_ADS_MCP_DIAGNOSTICS", "1")
  server = mock.Mock()
  with mock.patch.object(diagnostics, "_LOGGER") as logger:
    diagnostics.enable_diagnostics(server, "stdio")
  assert isinstance(
      server.add_middleware.call_args.args[0], diagnostics.ToolDiagnostics
  )
  message = logger.info.call_args.args
  assert message[1] == "stdio"
  assert len(message[-1]) == 16


def test_success_records_sizes_not_arguments_or_results():
  context = SimpleNamespace(
      message=SimpleNamespace(
          name="execute_gaql", arguments={"token": "private-input"}
      )
  )
  response = ToolResult(
      content={"secret": "private-result"},
      structured_content={"secret": "private-result"},
  )
  call_next = mock.AsyncMock(return_value=response)
  with mock.patch.object(diagnostics, "_LOGGER") as logger:
    result = asyncio.run(
        diagnostics.ToolDiagnostics().on_call_tool(context, call_next)
    )
  assert result is response
  assert "private" not in str(logger.info.call_args_list)
  args = logger.info.call_args.args
  assert args[-1] == len(response.model_dump_json().encode("utf-8"))


@pytest.mark.parametrize(
    "error", [ToolError("private-error"), asyncio.CancelledError()]
)
def test_failure_records_only_type_and_preserves_exception(error):
  context = SimpleNamespace(message=SimpleNamespace(name="execute_gaql"))
  call_next = mock.AsyncMock(side_effect=error)
  with mock.patch.object(diagnostics, "_LOGGER") as logger:
    with pytest.raises(type(error)) as caught:
      asyncio.run(
          diagnostics.ToolDiagnostics().on_call_tool(context, call_next)
      )
  assert caught.value is error
  assert logger.info.call_args.args[-1] == type(error).__name__
  assert "private-error" not in str(logger.info.call_args_list)


def test_unrecognized_tool_name_is_not_logged_as_user_text():
  context = SimpleNamespace(
      message=SimpleNamespace(name="injected\nprivate-input")
  )
  call_next = mock.AsyncMock(return_value=ToolResult(content="ok"))
  with mock.patch.object(diagnostics, "_LOGGER") as logger:
    asyncio.run(diagnostics.ToolDiagnostics().on_call_tool(context, call_next))
  assert "private" not in str(logger.info.call_args_list)
  assert logger.info.call_args.args[2] == "unrecognized"
