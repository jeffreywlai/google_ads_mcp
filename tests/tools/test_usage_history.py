"""Campaign-scoped change history and compact changed-field value tests."""

# pylint: disable=protected-access

from collections import OrderedDict
from datetime import date
from datetime import datetime
from datetime import timedelta
from unittest import mock

from ads_mcp.tools import api
from ads_mcp.tools import changes
from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.resources.types.change_event import ChangeEvent
from google.protobuf.field_mask_pb2 import FieldMask
import pytest

CUSTOMER_ID = "1234567890"
TODAY = date.today()
START = (TODAY - timedelta(days=6)).isoformat()
END = TODAY.isoformat()


@pytest.fixture(autouse=True)
def offline_history(monkeypatch):
  """Keeps account resolution and immutable snapshots offline and isolated."""
  monkeypatch.setattr(changes, "_account_today", lambda *_: (TODAY, "Etc/UTC"))
  monkeypatch.setattr(
      api, "get_ads_credential_cache_scope", lambda: "history-test"
  )
  monkeypatch.setattr(
      api, "customer_time_zone_for_credential", lambda *_: "Etc/UTC"
  )
  for name in (
      "_PAGED_QUERY_CACHE",
      "_PAGED_QUERY_LATEST",
      "_PAGED_QUERY_BUILDS",
      "_PAGED_QUERY_SNAPSHOT_GROUPS",
      "_PAGED_QUERY_GROUP_SNAPSHOTS",
      "_ACTIVE_PAGED_QUERY_GROUPS",
  ):
    monkeypatch.setattr(api, name, OrderedDict())


def _event(index=1):
  customer = f"customers/{CUSTOMER_ID}"
  return {
      "change_event.resource_name": f"{customer}/changeEvents/{index}",
      "change_event.change_resource_name": f"{customer}/campaigns/77",
      "change_event.change_resource_type": "CAMPAIGN",
      "change_event.campaign": f"customers/{CUSTOMER_ID}/campaigns/77",
      "change_event.ad_group": "",
      "change_event.resource_change_operation": "UPDATE",
      "change_event.changed_fields": {"paths": ["target_roas.target_roas"]},
      "change_event.old_resource": {
          "campaign": {"targetRoas": {"targetRoas": 0.0}, "name": "ignored"}
      },
      "change_event.new_resource": {
          "campaign": {"targetRoas": {"targetRoas": 4.0}, "name": "ignored"}
      },
  }


def test_event_scope_and_compact_values_preserve_raw_exact_snapshot():
  rows = [_event(1), _event(2)]
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as query:
    result = changes.list_change_events(
        CUSTOMER_ID,
        start_date=START,
        end_date=END,
        limit=1,
        campaign_id="77",
        ad_group_id="88",
        include_values=True,
    )
    continuation = result["continuation"]["arguments"]
    second = changes.list_change_events(**continuation)
    exact = list(
        api._get_export_snapshot_rows(
            result["bulk_export_call"]["arguments"]["snapshot_token"]
        )
    )

  sent_query = query.call_args.kwargs["query"]
  assert (
      "change_event.campaign = 'customers/1234567890/campaigns/77'"
      in sent_query
  )
  assert (
      "change_event.ad_group = 'customers/1234567890/adGroups/88'"
      in sent_query
  )
  assert "change_event.change_resource_name" in sent_query
  assert "change_event.old_resource" in sent_query
  assert query.call_count == 1
  assert continuation["campaign_id"] == "77"
  assert continuation["ad_group_id"] == "88"
  assert continuation["include_values"] is True
  for response in (result, second):
    assert response["change_events"][0]["change_event.field_changes"] == [
        {
            "field": "target_roas.target_roas",
            "old_value": 0.0,
            "new_value": 4.0,
            "old_value_available": True,
            "new_value_available": True,
        }
    ]
    assert "change_event.old_resource" not in response["change_events"][0]
  assert exact == rows


@pytest.mark.parametrize(
    "changed_argument",
    [{"campaign_id": "78"}, {"ad_group_id": "89"}, {"include_values": False}],
)
def test_event_continuation_rejects_changed_scope_or_value_mode(
    changed_argument,
):
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=[_event(1), _event(2)]
  ) as query:
    result = changes.list_change_events(
        CUSTOMER_ID,
        start_date=START,
        end_date=END,
        limit=1,
        campaign_id="77",
        ad_group_id="88",
        include_values=True,
    )
    arguments = {**result["continuation"]["arguments"], **changed_argument}
    with pytest.raises(ToolError, match="page_token"):
      changes.list_change_events(**arguments)
  assert query.call_count == 1


def test_status_scope_entities_and_continuation_are_bound():
  rows = [{"change_status.campaign": "customers/1234567890/campaigns/77"}] * 2
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=rows
  ) as query:
    result = changes.list_change_statuses(
        CUSTOMER_ID,
        start_date=START,
        end_date=END,
        limit=1,
        campaign_id="77",
        ad_group_id="88",
    )
    with pytest.raises(ToolError, match="page_token"):
      changes.list_change_statuses(
          **{**result["continuation"]["arguments"], "campaign_id": "78"}
      )
  sent_query = query.call_args.kwargs["query"]
  assert (
      "change_status.campaign = 'customers/1234567890/campaigns/77'"
      in sent_query
  )
  assert (
      "change_status.ad_group = 'customers/1234567890/adGroups/88'"
      in sent_query
  )
  assert "change_status.campaign_shared_set" in sent_query
  assert query.call_count == 1


@pytest.mark.parametrize(
    "tool",
    [
        changes.list_change_events,
        changes.list_change_statuses,
        changes.get_change_history_extended,
        changes.export_change_history_csv,
    ],
)
@pytest.mark.parametrize("invalid_id", ["77' OR true", "0", "-1", True])
def test_invalid_scope_is_rejected_before_account_lookup(tool, invalid_id):
  with mock.patch.object(changes, "_account_today") as account:
    with pytest.raises(ToolError, match="campaign_id"):
      tool(CUSTOMER_ID, campaign_id=invalid_id)
  account.assert_not_called()


def test_export_filters_both_sources_without_dropping_raw_values():
  collection = {
      "fragment_paths": [],
      "row_count": 0,
      "query_count": 1,
      "complete": True,
      "unresolved_windows": [],
  }
  with mock.patch.object(
      changes, "_collect_complete_change_rows", return_value=collection
  ) as collect:
    with mock.patch.object(
        changes,
        "merge_temp_csv_files",
        return_value=("/tmp/history.csv", [], 0),
    ):
      result = changes.export_change_history_csv(
          CUSTOMER_ID,
          start_date=START,
          end_date=END,
          campaign_id="77",
          ad_group_id="88",
      )
  assert collect.call_count == 2
  for call, source in zip(
      collect.call_args_list, ("change_status", "change_event")
  ):
    query = call.args[0](
        datetime.combine(TODAY, datetime.min.time()),
        datetime.combine(TODAY + timedelta(days=1), datetime.min.time()),
    )
    assert f"{source}.campaign = 'customers/1234567890/campaigns/77'" in query
    assert f"{source}.ad_group = 'customers/1234567890/adGroups/88'" in query
    if source == "change_event":
      assert "change_event.old_resource" in query
      assert "change_event.new_resource" in query
  assert result["entity_scope"]["campaign_id"] == "77"
  assert result["available_data_complete"] is True


def test_extended_status_only_types_keep_scope_and_source_coverage():
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=[]
  ) as query:
    result = changes.get_change_history_extended(
        CUSTOMER_ID,
        resource_types=["CAMPAIGN_SHARED_SET"],
        campaign_id="77",
        include_values=True,
    )
  assert query.call_count == 1
  assert "FROM change_status" in query.call_args.kwargs["query"]
  assert (
      "change_status.campaign = 'customers/1234567890/campaigns/77'"
      in query.call_args.kwargs["query"]
  )
  assert (
      result["resource_type_coverage"]["change_event"]["query_skipped"] is True
  )
  assert result["change_event_coverage"]["available"] is False
  assert result["entity_scope"]["campaign_id"] == "77"


def test_direct_status_only_request_names_existing_combined_tool():
  with pytest.raises(ToolError, match="get_change_history_extended"):
    changes.list_change_events(
        CUSTOMER_ID, change_resource_types=["CAMPAIGN_SHARED_SET"]
    )


def test_proto_values_keep_zero_and_false_and_mark_unavailable_fields():
  event = ChangeEvent()
  event.change_resource_type = "CAMPAIGN"
  event.old_resource.campaign.target_roas.target_roas = 0.0
  event.new_resource.campaign.target_roas.target_roas = 4.0
  event.new_resource.campaign.network_settings.target_search_network = False
  row = _event()
  row["change_event.old_resource"] = api.format_value(event.old_resource)
  row["change_event.new_resource"] = api.format_value(event.new_resource)
  row["change_event.changed_fields"] = api.format_value(
      FieldMask(
          paths=[
              "target_roas.target_roas",
              "network_settings.target_search_network",
              "missing_field",
          ]
      )
  )
  compact = changes._compact_change_values(row)["change_event.field_changes"]
  assert compact[0]["old_value"] == 0.0
  assert compact[0]["old_value_available"] is True
  assert compact[1]["new_value"] is False
  assert compact[1]["new_value_available"] is True
  assert compact[1]["old_value_available"] is False
  assert compact[2]["new_value"] is None
  assert compact[2]["new_value_available"] is False


def test_oversized_event_remains_atomic_and_exact_export_keeps_values():
  row = _event()
  row["change_event.changed_fields"] = {"paths": ["name"]}
  row["change_event.new_resource"] = {
      "campaign": {"name": "x" * (api.INLINE_PAGE_BYTE_LIMIT + 100)}
  }
  with mock.patch.object(
      api, "_iter_gaql_query_attempt", return_value=[row, _event(2)]
  ):
    result = changes.list_change_events(
        CUSTOMER_ID,
        start_date=START,
        end_date=END,
        limit=1,
        campaign_id="77",
        include_values=True,
    )
    exact = list(
        api._get_export_snapshot_rows(
            result["bulk_export_call"]["arguments"]["snapshot_token"]
        )
    )
  assert result["complete_inline"] is False
  assert "change_event.field_changes" not in result["change_events"][0]
  assert exact[0] == row


@pytest.mark.parametrize(
    "tool", [changes.list_change_events, changes.list_change_statuses]
)
def test_capped_scoped_preview_preserves_scope_in_complete_export_hint(tool):
  page = {"rows": [], "next_page_token": None, "total_results_count": 10000}
  with mock.patch.object(changes, "run_gaql_query_page", return_value=page):
    result = tool(CUSTOMER_ID, campaign_id="77", ad_group_id="88")
  assert result["next_page_token"] is None
  assert result["complete_inline"] is False
  call = result["bulk_export_call"]
  assert call["tool"] == "export_change_history_csv"
  assert call["arguments"]["campaign_id"] == "77"
  assert call["arguments"]["ad_group_id"] == "88"


def test_extended_scope_and_values_continue_both_sources():
  status_rows = [
      {"change_status.campaign": "customers/1234567890/campaigns/77"}
  ] * 2
  with mock.patch.object(
      api,
      "_iter_gaql_query_attempt",
      side_effect=[status_rows, [_event(1), _event(2)]],
  ):
    result = changes.get_change_history_extended(
        CUSTOMER_ID,
        start_date=START,
        end_date=END,
        limit=1,
        campaign_id="77",
        ad_group_id="88",
        include_values=True,
    )
  for source in ("change_status", "change_event"):
    arguments = result["continuation_guidance"][source]["arguments"]
    assert arguments["campaign_id"] == "77"
    assert arguments["ad_group_id"] == "88"
    if source == "change_event":
      assert arguments["include_values"] is True
  assert (
      result["recent_change_events"][0]["change_event.field_changes"][0][
          "new_value"
      ]
      == 4.0
  )


def test_shared_budget_export_fallback_preserves_scope():
  omitted = {"shared_inline_omitted_count": 1}
  guidance = changes._preview_continuation_guidance(
      omitted,
      omitted,
      customer_id=CUSTOMER_ID,
      status_window={"start_date": START, "end_date": END},
      event_window={"start_date": START, "end_date": END},
      status_resource_types=["CAMPAIGN"],
      event_resource_types=["CAMPAIGN"],
      limit=100,
      login_customer_id=None,
      scope={"campaign_id": "77", "ad_group_id": "88"},
      include_values=True,
  )
  for call in guidance.values():
    assert call["tool"] == "export_change_history_csv"
    assert call["arguments"]["campaign_id"] == "77"
    assert call["arguments"]["ad_group_id"] == "88"
    assert "include_values" not in call["arguments"]
