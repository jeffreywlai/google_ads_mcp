"""Offline directory-race regressions for explicitly requested CSV exports."""

# pylint: disable=protected-access

from contextlib import contextmanager
import errno
from functools import partial
from pathlib import Path
import stat
import subprocess
import sys
from unittest import mock

import pytest

from ads_mcp.tools import api

_ROWS = [{"campaign.id": "1"}]
_ORIGINAL = "original destination\n"
_OUTSIDE = "outside destination\n"
_LATER_WRITER = b"later writer's CSV\n"
_LAST_WRITER = b"last writer's CSV\n"


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


def _install_other_csv(directory, name, data, replace):
  replacement = directory / "writer.csv"
  replacement.write_bytes(data)
  replace(replacement, directory / name)


@contextmanager
def _plant_untrusted_staging_csv(parent, data):
  """Plants data immediately after the writer creates a new staging folder."""
  original_mkdir = api.os.mkdir
  staging_paths = []

  def create_staging(name, *args, **kwargs):
    original_mkdir(name, *args, **kwargs)
    staging_paths.append(parent / name)
    (staging_paths[-1] / "export.csv").write_bytes(data)

  with mock.patch.object(api.os, "mkdir", side_effect=create_staging):
    yield staging_paths


@contextmanager
def _interfere_during_rollback(parent, outside, output, interference):
  """Injects another writer at either rollback implementation's boundary."""
  native = {
      name: getattr(api.os, name)
      for name in ("link", "replace", "rename", "stat")
  }
  retained = []
  injected = []

  def install(data):
    _install_other_csv(retained[0], output.name, data, native["replace"])
    injected.append(data)

  def publish(method, source, destination, *args, **kwargs):
    result = native[method](source, destination, *args, **kwargs)
    if (
        destination == output.name
        and kwargs.get("dst_dir_fd") is not None
        and not retained
    ):
      retained.append(_swap_directory(parent, outside))
    return result

  def observe_stat(path, *args, **kwargs):
    result = native["stat"](path, *args, **kwargs)
    if (
        interference == "before_capture"
        and path == output.name
        and kwargs.get("dir_fd") is not None
        and retained
        and not injected
    ):
      # The former rollback returned this now-stale identity to its caller.
      install(_LATER_WRITER)
    return result

  def capture(source, destination, *args, **kwargs):
    is_capture = (
        source == output.name
        and destination == "published.csv"
        and kwargs.get("src_dir_fd") is not None
        and retained
    )
    if is_capture and interference in ("before_capture", "blocked_putback"):
      install(_LATER_WRITER)
    result = native["rename"](source, destination, *args, **kwargs)
    if is_capture and interference in ("after_capture", "blocked_putback"):
      install(
          _LAST_WRITER if interference == "blocked_putback" else _LATER_WRITER
      )
    return result

  with (
      mock.patch.object(api.os, "link", side_effect=partial(publish, "link")),
      mock.patch.object(
          api.os, "replace", side_effect=partial(publish, "replace")
      ),
      mock.patch.object(api.os, "rename", side_effect=capture),
      mock.patch.object(api.os, "stat", side_effect=observe_stat),
  ):
    yield retained, injected


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


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("swap_timing", ["before", "after"])
def test_directory_swap_at_publication_rolls_back_the_export(
    tmp_path, monkeypatch, overwrite, swap_timing
):
  """Publication races cannot leave a new export or replace previous data."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, overwrite)
  outside_before = _directory_files(outside)
  publish_method = "link"
  original_publish = getattr(api.os, publish_method)
  retained = []

  def publish(source, destination, *args, **kwargs):
    is_publication = (
        destination == output.name
        and kwargs.get("dst_dir_fd") is not None
        and not retained
    )
    if is_publication and swap_timing == "before":
      retained.append(_swap_directory(parent, outside))
    result = original_publish(source, destination, *args, **kwargs)
    if is_publication and swap_timing == "after":
      retained.append(_swap_directory(parent, outside))
    return result

  with (
      mock.patch.object(api.os, publish_method, side_effect=publish),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError),
  ):
    _export(output, overwrite)

  assert len(retained) == 1
  assert _directory_files(outside) == outside_before
  expected = {output.name: _ORIGINAL.encode()} if overwrite else {}
  assert _directory_files(retained[0]) == expected
  assert {path.name for path in retained[0].iterdir()} == set(expected)


def test_failed_restoration_preserves_the_previous_csv_in_private_staging(
    tmp_path, monkeypatch
):
  """A rollback failure keeps recoverable previous data and reports failure."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, True)
  output.chmod(0o600)
  outside_before = _directory_files(outside)
  original_link = api.os.link
  retained = []

  def publish_or_restore(source, destination, *args, **kwargs):
    if source == "previous.csv":
      raise OSError("synthetic restoration failure")
    result = original_link(source, destination, *args, **kwargs)
    if destination == output.name and not retained:
      retained.append(_swap_directory(parent, outside))
    return result

  with (
      mock.patch.object(api.os, "link", side_effect=publish_or_restore),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError, match="previous CSV is retained"),
  ):
    _export(output, True)

  assert len(retained) == 1
  assert _directory_files(outside) == outside_before
  previous_paths = list(retained[0].glob("*/previous.csv"))
  assert len(previous_paths) == 1
  previous = previous_paths[0]
  assert previous.read_bytes() == _ORIGINAL.encode()
  assert stat.S_IMODE(previous.stat().st_mode) == 0o600
  assert stat.S_IMODE(previous.parent.stat().st_mode) == 0o700
  assert {path.name for path in previous.parent.iterdir()} == {"previous.csv"}


@pytest.mark.parametrize("published_change", ["deleted", "replaced"])
def test_changed_published_file_is_not_touched_and_previous_csv_is_retained(
    tmp_path, monkeypatch, published_change
):
  """Rollback preserves other writers' files and recoverable previous data."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, True)
  output.chmod(0o600)
  outside_before = _directory_files(outside)
  native = {"link": api.os.link, "replace": api.os.replace}
  retained = []
  other_data = b"another writer's CSV\n"

  def publish(source, destination, *args, **kwargs):
    result = native["link"](source, destination, *args, **kwargs)
    if (
        destination == output.name
        and kwargs.get("dst_dir_fd") is not None
        and not retained
    ):
      retained.append(_swap_directory(parent, outside))
      published = retained[0] / output.name
      if published_change == "deleted":
        published.unlink()
      else:
        replacement = retained[0] / "replacement.csv"
        replacement.write_bytes(other_data)
        native["replace"](replacement, published)
    return result

  with (
      mock.patch.object(api.os, "link", side_effect=publish),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError, match="previous CSV is retained"),
  ):
    _export(output, True)

  assert len(retained) == 1
  assert _directory_files(outside) == outside_before
  previous_paths = list(retained[0].glob("*/previous.csv"))
  assert len(previous_paths) == 1
  previous = previous_paths[0]
  expected = {str(previous.relative_to(retained[0])): _ORIGINAL.encode()}
  if published_change == "replaced":
    expected[output.name] = other_data
  assert _directory_files(retained[0]) == expected
  assert stat.S_IMODE(previous.stat().st_mode) == 0o600
  assert stat.S_IMODE(previous.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize(
    ("overwrite", "interference"),
    [
        (False, "before_capture"),
        (True, "before_capture"),
        (True, "after_capture"),
        (True, "blocked_putback"),
    ],
)
def test_rollback_interference_preserves_every_other_writers_csv(
    tmp_path, monkeypatch, overwrite, interference
):
  """Rollback never deletes or overwrites a later writer's destination."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, overwrite)
  if overwrite:
    output.chmod(0o600)
  outside_before = _directory_files(outside)

  with (
      _interfere_during_rollback(parent, outside, output, interference) as (
          retained,
          injected,
      ),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError),
  ):
    _export(output, overwrite)

  assert len(retained) == 1
  assert len(injected) == (2 if interference == "blocked_putback" else 1)
  assert _directory_files(outside) == outside_before
  assert (retained[0] / output.name).read_bytes() == injected[-1]
  expected = {output.name: injected[-1]}
  previous_paths = list(retained[0].glob("*/previous.csv"))
  assert len(previous_paths) == int(overwrite)
  if overwrite:
    expected[str(previous_paths[0].relative_to(retained[0]))] = (
        _ORIGINAL.encode()
    )
    assert stat.S_IMODE(previous_paths[0].parent.stat().st_mode) == 0o700
  captured_paths = list(retained[0].glob("*/published.csv"))
  assert len(captured_paths) == int(interference == "blocked_putback")
  if captured_paths:
    expected[str(captured_paths[0].relative_to(retained[0]))] = _LATER_WRITER
  assert _directory_files(retained[0]) == expected
  assert {path.name for path in retained[0].iterdir()} == {
      name.split("/")[0] for name in expected
  }


@pytest.mark.parametrize("writer_timing", ["before_capture", "after_capture"])
def test_overwrite_preserves_a_writer_arriving_at_previous_file_capture(
    tmp_path, monkeypatch, writer_timing
):
  """Overwrite publication and recovery both respect another writer's file."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, True)
  output.chmod(0o600)
  outside_before = _directory_files(outside)
  native = {"rename": api.os.rename, "replace": api.os.replace}
  captured = []

  def capture_previous(source, destination, *args, **kwargs):
    is_capture = (
        source == output.name
        and destination == "previous.csv"
        and kwargs.get("src_dir_fd") is not None
    )
    if is_capture and writer_timing == "before_capture":
      native["rename"](output, parent / "retained-original.csv")
      _install_other_csv(parent, output.name, _LATER_WRITER, native["replace"])
    result = native["rename"](source, destination, *args, **kwargs)
    if is_capture:
      captured.append(True)
      if writer_timing == "after_capture":
        _install_other_csv(
            parent, output.name, _LATER_WRITER, native["replace"]
        )
    return result

  with (
      mock.patch.object(api.os, "rename", side_effect=capture_previous),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError),
  ):
    _export(output, True)

  assert len(captured) == 1
  assert _directory_files(outside) == outside_before
  expected = {output.name: _LATER_WRITER}
  previous_paths = list(parent.glob("*/previous.csv"))
  if writer_timing == "before_capture":
    expected["retained-original.csv"] = _ORIGINAL.encode()
    assert not previous_paths
  else:
    assert len(previous_paths) == 1
    expected[str(previous_paths[0].relative_to(parent))] = _ORIGINAL.encode()
    assert stat.S_IMODE(previous_paths[0].parent.stat().st_mode) == 0o700
  assert _directory_files(parent) == expected
  assert {path.name for path in parent.iterdir()} == {
      name.split("/")[0] for name in expected
  }


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("leaf_change", ["replaced", "deleted", "symlink"])
def test_changed_published_leaf_is_refused_even_when_parent_is_unchanged(
    tmp_path, monkeypatch, overwrite, leaf_change
):
  """Successful export paths must still identify the CSV that was written."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, overwrite)
  if overwrite:
    output.chmod(0o600)
  outside_before = _directory_files(outside)
  native = {"link": api.os.link, "replace": api.os.replace}
  changed = []

  def publish(source, destination, *args, **kwargs):
    result = native["link"](source, destination, *args, **kwargs)
    if source == "export.csv" and destination == output.name and not changed:
      changed.append(True)
      if leaf_change == "replaced":
        _install_other_csv(
            parent, output.name, _LATER_WRITER, native["replace"]
        )
      else:
        output.unlink()
        if leaf_change == "symlink":
          output.symlink_to(outside / "victim.txt")
    return result

  with (
      mock.patch.object(api.os, "link", side_effect=publish),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError),
  ):
    _export(output, overwrite)

  assert len(changed) == 1
  assert _directory_files(outside) == outside_before
  expected = {}
  if leaf_change == "deleted":
    assert not output.exists()
    assert not output.is_symlink()
  else:
    expected[output.name] = (
        _LATER_WRITER if leaf_change == "replaced" else _OUTSIDE.encode()
    )
    if leaf_change == "symlink":
      assert output.is_symlink()
      assert output.readlink() == outside / "victim.txt"
  previous_paths = list(parent.glob("*/previous.csv"))
  assert len(previous_paths) == int(overwrite)
  if previous_paths:
    expected[str(previous_paths[0].relative_to(parent))] = _ORIGINAL.encode()
    assert stat.S_IMODE(previous_paths[0].parent.stat().st_mode) == 0o700
  assert _directory_files(parent) == expected
  assert {path.name for path in parent.iterdir()} == {
      name.split("/")[0] for name in expected
  }


@pytest.mark.parametrize("mode", [0o200, 0o000])
def test_overwrite_preserves_owned_output_without_read_permission(
    tmp_path, monkeypatch, mode
):
  """Safe overwrite does not require reading an existing owned CSV."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, True)
  outside_before = _directory_files(outside)
  output.chmod(mode)

  try:
    with mock.patch.object(api, "run_gaql_query", return_value=_ROWS):
      result = _export(output, True)
    assert result["file_path"] == str(output)
    assert stat.S_IMODE(output.stat().st_mode) == mode
  finally:
    output.chmod(0o600)

  assert output.read_bytes() == b"campaign.id\r\n1\r\n"
  assert _directory_files(outside) == outside_before
  assert {path.name for path in parent.iterdir()} == {output.name}


@pytest.mark.skipif(
    sys.platform != "darwin", reason="Native macOS ACL required"
)
def test_inherited_macos_acl_cannot_make_export_staging_shared(
    tmp_path, monkeypatch
):
  """Inherited access remains unsafe even with a staging mode of 0700."""
  _, parent, outside, output = _export_paths(tmp_path, monkeypatch, True)
  outside_before = _directory_files(outside)
  subprocess.run(
      [
          "/bin/chmod",
          "+a",
          "everyone allow list,search,add_file,delete_child,"
          "directory_inherit,file_inherit",
          str(parent),
      ],
      check=True,
      capture_output=True,
  )

  try:
    with (
        mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
        pytest.raises(api.ToolError, match="private export staging"),
    ):
      _export(output, True)
  finally:
    subprocess.run(["/bin/chmod", "-N", str(parent)], check=True)

  assert _directory_files(parent) == {output.name: _ORIGINAL.encode()}
  assert {path.name for path in parent.iterdir()} == {output.name}
  assert _directory_files(outside) == outside_before


def test_nonempty_staging_acl_refuses_to_delete_planted_data(
    tmp_path, monkeypatch
):
  """ACL-protected checks happen before any cleanup can trust stage entries."""
  _, parent, _, output = _export_paths(tmp_path, monkeypatch, True)
  planted_data = b"untrusted ACL-stage CSV\n"

  with (
      _plant_untrusted_staging_csv(parent, planted_data) as staging_paths,
      mock.patch.object(api, "_export_staging_has_acl", return_value=True),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError, match="private export staging"),
  ):
    _export(output, True)

  assert len(staging_paths) == 1
  assert stat.S_IMODE(staging_paths[0].stat().st_mode) == 0o700
  assert _directory_files(parent) == {
      output.name: _ORIGINAL.encode(),
      f"{staging_paths[0].name}/export.csv": planted_data,
  }


def test_staging_acl_inspection_error_fails_closed(tmp_path, monkeypatch):
  """ACL inspection failures neither publish CSV data nor trust its cleanup."""
  _, parent, _, output = _export_paths(tmp_path, monkeypatch, True)
  planted_data = b"untrusted uninspected-stage CSV\n"

  with (
      _plant_untrusted_staging_csv(parent, planted_data) as staging_paths,
      mock.patch.object(
          api,
          "_export_staging_has_acl",
          side_effect=OSError(errno.EIO, "synthetic ACL inspection failure"),
      ),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError),
  ):
    _export(output, True)

  assert len(staging_paths) == 1
  assert _directory_files(parent) == {
      output.name: _ORIGINAL.encode(),
      f"{staging_paths[0].name}/export.csv": planted_data,
  }


def test_nonempty_pristine_staging_is_refused_without_deleting_existing_csv(
    tmp_path, monkeypatch
):
  """Owned mode-0700 staging must also start empty before it becomes trusted."""
  _, parent, _, output = _export_paths(tmp_path, monkeypatch, True)
  planted_data = b"untrusted preexisting-stage CSV\n"

  with (
      _plant_untrusted_staging_csv(parent, planted_data) as staging_paths,
      mock.patch.object(api, "_export_staging_has_acl", return_value=False),
      mock.patch.object(api, "run_gaql_query", return_value=_ROWS),
      pytest.raises(api.ToolError, match="private export staging"),
  ):
    _export(output, True)

  assert len(staging_paths) == 1
  assert stat.S_IMODE(staging_paths[0].stat().st_mode) == 0o700
  assert staging_paths[0].stat().st_uid == api.os.geteuid()
  assert _directory_files(parent) == {
      output.name: _ORIGINAL.encode(),
      f"{staging_paths[0].name}/export.csv": planted_data,
  }
