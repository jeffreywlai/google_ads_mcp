"""Offline directory-race regressions for explicitly requested CSV exports."""

# pylint: disable=protected-access

from pathlib import Path
from unittest import mock

import pytest

from ads_mcp.tools import api

_ROWS = [{"campaign.id": "1"}]
_ORIGINAL = "original destination\n"
_OUTSIDE = "outside destination\n"


def _export_paths(tmp_path, monkeypatch, overwrite):
  allowed = tmp_path / "exports"
  parent = allowed / "nested"
  parent.mkdir(parents=True)
  outside = tmp_path / "outside"
  (outside / "nested").mkdir(parents=True)
  output = parent / "audit.csv"
  (outside / "victim.txt").write_text(_OUTSIDE, encoding="utf-8")
  if overwrite:
    output.write_text(_ORIGINAL, encoding="utf-8")
    (outside / output.name).write_text(_OUTSIDE, encoding="utf-8")
    (outside / "nested" / output.name).write_text(_OUTSIDE, encoding="utf-8")
  monkeypatch.setenv("GOOGLE_ADS_MCP_EXPORT_DIR", str(allowed))
  return allowed, parent, outside, output


def _directory_files(directory):
  return {
      str(path.relative_to(directory)): path.read_bytes()
      for path in directory.rglob("*")
      if path.is_file()
  }


def _swap_directory(directory, outside):
  retained = directory.with_name(f"{directory.name}_retained")
  directory.rename(retained)
  directory.symlink_to(outside, target_is_directory=True)
  return retained


def _export(output, overwrite):
  return api.export_gaql_csv(
      query="SELECT campaign.id FROM campaign",
      customer_id="123",
      output_path=str(output),
      overwrite=overwrite,
  )


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("swapped_directory", ["parent", "export_root"])
def test_directory_swap_during_read_cannot_redirect_export(
    tmp_path, monkeypatch, overwrite, swapped_directory
):
  """Validated paths cannot follow a new outside symlink during saving."""
  allowed, parent, outside, output = _export_paths(
      tmp_path, monkeypatch, overwrite
  )
  outside_before = _directory_files(outside)
  retained = []
  original_cell_value = api._csv_cell_value

  def read_rows(**_kwargs):
    directory = parent if swapped_directory == "parent" else allowed
    retained.append(_swap_directory(directory, outside))
    return _ROWS

  def serialize(value):
    # Catch even a temporary outside write that later cleanup would hide.
    assert _directory_files(outside) == outside_before
    return original_cell_value(value)

  with (
      mock.patch.object(api, "run_gaql_query", side_effect=read_rows),
      mock.patch.object(api, "_csv_cell_value", side_effect=serialize),
      pytest.raises(api.ToolError),
  ):
    _export(output, overwrite)

  assert len(retained) == 1
  assert _directory_files(outside) == outside_before
  retained_parent = (
      retained[0]
      if swapped_directory == "parent"
      else retained[0] / parent.name
  )
  expected = {output.name: _ORIGINAL.encode()} if overwrite else {}
  assert _directory_files(retained_parent) == expected


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("serialization_failure", [False, True])
def test_parent_swap_during_save_never_publishes_into_outside_directory(
    tmp_path, monkeypatch, overwrite, serialization_failure
):
  """Publishing and cleanup stay with the original parent after a rename."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, overwrite)
  outside_before = _directory_files(outside)
  original_cell_value = api._csv_cell_value
  retained = []

  def serialize(value):
    retained.append(_swap_directory(parent, outside))
    if serialization_failure:
      raise ValueError("synthetic serialization failure")
    return original_cell_value(value)

  with (
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      mock.patch.object(api, "_csv_cell_value", side_effect=serialize),
  ):
    if serialization_failure:
      with pytest.raises(ValueError, match="synthetic serialization failure"):
        _export(output, overwrite)
    else:
      with pytest.raises(api.ToolError):
        _export(output, overwrite)

  assert len(retained) == 1
  assert _directory_files(outside) == outside_before
  expected = {output.name: _ORIGINAL.encode()} if overwrite else {}
  assert _directory_files(retained[0]) == expected


@pytest.mark.parametrize("overwrite", [False, True])
def test_shared_export_opener_refuses_an_ancestor_symlink(
    tmp_path, monkeypatch, overwrite
):
  """The shared opener protects parent components as well as the filename."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, overwrite)
  resolved_path = api._resolve_export_path(str(output), overwrite)
  outside_before = _directory_files(outside)
  retained = _swap_directory(parent, outside)

  with pytest.raises(api.ToolError, match="Unable to write"):
    with api._open_export_file(resolved_path, overwrite) as csv_file:
      csv_file.write("unexpected outside export\n")

  assert _directory_files(outside) == outside_before
  expected = {output.name: _ORIGINAL.encode()} if overwrite else {}
  assert _directory_files(retained) == expected


def test_unsupported_directory_operations_refuse_explicit_paths_before_read(
    tmp_path, monkeypatch
):
  """A platform without secure path support retains implicit temp exports."""
  _, parent, _, output = _export_paths(tmp_path, monkeypatch, False)
  monkeypatch.setattr(api.tempfile, "gettempdir", lambda: str(tmp_path))

  with (
      mock.patch.object(api, "_SECURE_EXPORT_PATHS_SUPPORTED", False),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS) as read,
  ):
    with pytest.raises(api.ToolError, match="directory-descriptor support"):
      _export(output, False)
    read.assert_not_called()
    assert not _directory_files(parent)

    result = api.export_gaql_csv(
        query="SELECT campaign.id FROM campaign", customer_id="123"
    )
    temp_path = Path(result["file_path"])
    try:
      assert temp_path.parent == tmp_path
      assert temp_path.read_bytes() == b"campaign.id\r\n1\r\n"
      assert result["row_count"] == 1
    finally:
      temp_path.unlink()
    read.assert_called_once()


@pytest.mark.parametrize("unsafe_staging", ["group", "other", "owner"])
def test_unsafe_staging_is_refused_without_deleting_untrusted_data(
    tmp_path, monkeypatch, unsafe_staging
):
  """Unsafe staging never becomes trusted enough to write or delete its CSV."""
  _, parent, _, output = _export_paths(tmp_path, monkeypatch, True)
  original_mkdir = api.os.mkdir
  staged_paths = []
  untrusted_data = b"untrusted CSV data\n"
  mode = {"group": 0o750, "other": 0o705, "owner": 0o700}[unsafe_staging]
  if unsafe_staging == "owner":
    effective_uid = api.os.geteuid()
    monkeypatch.setattr(api.os, "geteuid", lambda: effective_uid + 1)

  def create_staging(name, *args, **kwargs):
    original_mkdir(name, *args, **kwargs)
    staged_path = parent / name
    staged_paths.append(staged_path)
    staged_path.chmod(mode)
    (staged_path / "export.csv").write_bytes(untrusted_data)

  with (
      mock.patch.object(api.os, "mkdir", side_effect=create_staging),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError, match="private export staging"),
  ):
    _export(output, True)

  assert len(staged_paths) == 1
  assert _directory_files(parent) == {
      output.name: _ORIGINAL.encode(),
      f"{staged_paths[0].name}/export.csv": untrusted_data,
  }
