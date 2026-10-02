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

"""Offline v24 regressions for campaign location targets and exclusions."""

import asyncio
import csv
import json
from unittest import mock

from fastmcp import Client
from fastmcp.exceptions import ToolError
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v25.errors.types.errors import GoogleAdsError
from google.ads.googleads.v25.errors.types.errors import GoogleAdsFailure
from google.ads.googleads.v25.resources.types.campaign_criterion import (
    CampaignCriterion,
)
from google.ads.googleads.v25.services.types.campaign_criterion_service import (
    CampaignCriterionOperation,
    MutateCampaignCriteriaRequest,
    MutateCampaignCriteriaResponse,
)
from google.api_core import exceptions
from google.protobuf.any_pb2 import Any as ProtoAny
from google.rpc.status_pb2 import Status
import pytest

from ads_mcp.coordinator import mcp_server
from ads_mcp.tools import api
from ads_mcp.tools import campaigns
from ads_mcp.tools import docs
from ads_mcp.tools._gaql import preprocess_gaql_query


CUSTOMER_ID = "1234567890"
CAMPAIGN_ID = "222"


def _resource(criterion_id, customer_id=CUSTOMER_ID, campaign_id=CAMPAIGN_ID):
  return (
      f"customers/{customer_id}/campaignCriteria/{campaign_id}~{criterion_id}"
  )


def _row(criterion_id, **overrides):
  row = {
      "campaign.id": CAMPAIGN_ID,
      "campaign_criterion.resource_name": _resource(criterion_id),
      "campaign_criterion.criterion_id": criterion_id,
      "campaign_criterion.type": "LOCATION",
      "campaign_criterion.status": "ENABLED",
  }
  row.update(overrides)
  return row


def _response(resource_names=(), failed_indexes=()):
  errors = [
      GoogleAdsError(
          message="Invalid location",
          location={
              "field_path_elements": [
                  {"field_name": "operations", "index": index}
              ]
          },
      )
      for index in failed_indexes
  ]
  status = Status()
  if errors:
    detail = ProtoAny()
    detail.Pack(GoogleAdsFailure.pb(GoogleAdsFailure(errors=errors)))
    status = Status(code=3, message="Invalid location", details=[detail])
  return MutateCampaignCriteriaResponse(
      results=[{"resource_name": name} for name in resource_names],
      partial_failure_error=status,
  )


@pytest.fixture(autouse=True, name="offline_client")
def fake_ads_client():
  """Uses real v24 operations while preventing all credential/API access."""
  with (
      mock.patch.object(campaigns, "get_ads_client") as get_client,
      mock.patch.object(campaigns, "run_gaql_query") as read,
  ):
    client = get_client.return_value
    service = client.get_service.return_value

    def make_operation(type_name):
      assert type_name == "CampaignCriterionOperation"
      return CampaignCriterionOperation()

    client.get_type.side_effect = make_operation
    service.mutate_campaign_criteria.return_value = _response(
        [_resource("91")]
    )
    read.return_value = [_row("91")]
    yield get_client, service, read


@pytest.mark.parametrize("negative", [False, True])
@pytest.mark.parametrize(
    "geo_ids",
    [
        ["2840", "geoTargetConstants/2036"],
        '["2840", "geoTargetConstants/2036"]',
        "2840,geoTargetConstants/2036",
    ],
)
def test_add_builds_v24_locations_and_reports_attachment_ids(
    offline_client, negative, geo_ids
):
  get_client, service, read = offline_client
  service.mutate_campaign_criteria.return_value = _response(
      [_resource("91"), _resource("92")]
  )

  result = campaigns.add_campaign_location_targets(
      "123-456-7890",
      CAMPAIGN_ID,
      geo_ids,
      negative,
      login_customer_id="987 654 3210",
  )

  get_client.assert_called_once_with("9876543210")
  read.assert_not_called()
  request = MutateCampaignCriteriaRequest(
      **service.mutate_campaign_criteria.call_args.kwargs["request"]
  )
  assert request.customer_id == CUSTOMER_ID
  assert request.validate_only is False
  assert request.partial_failure is False
  assert len(request.operations) == 2
  for operation, geo_id in zip(request.operations, ["2840", "2036"]):
    assert CampaignCriterionOperation.pb(operation).WhichOneof(
        "operation"
    ) == ("create")
    assert operation.create.campaign == (
        f"customers/{CUSTOMER_ID}/campaigns/{CAMPAIGN_ID}"
    )
    assert operation.create.negative is negative
    assert operation.create.location.geo_target_constant == (
        f"geoTargetConstants/{geo_id}"
    )
    assert CampaignCriterion.pb(operation.create).WhichOneof("criterion") == (
        "location"
    )
  assert result["created_criterion_ids"] == ["91", "92"]
  assert result["mutated_count"] == result["submitted_count"] == 2
  assert result["successes"] == [
      {"index": 0, "resource_name": _resource("91"), "criterion_id": "91"},
      {"index": 1, "resource_name": _resource("92"), "criterion_id": "92"},
  ]
  assert result["failures"] == []
  assert "partial_failure_error" not in result


@pytest.mark.parametrize(
    "geo_ids",
    [
        [],
        "",
        ["0"],
        ["-1"],
        [True],
        [1.5],
        [str(2**63)],
        ["2840", "geoTargetConstants/2840"],
        ["geoTargetConstants/02840"],
        ["geoTargetConstants/2840/"],
        ["geoTargetConstants/2840 "],
        ["geoTargetConstants/abc"],
        ["customers/123/geoTargetConstants/2840"],
        ["2840 OR 1=1"],
    ],
)
def test_invalid_geo_ids_never_create_client(offline_client, geo_ids):
  get_client, service, read = offline_client
  with pytest.raises(ToolError):
    campaigns.add_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, geo_ids, False
    )
  get_client.assert_not_called()
  service.mutate_campaign_criteria.assert_not_called()
  read.assert_not_called()


@pytest.mark.parametrize("negative", ["false", "true", 0, 1, None])
def test_negative_requires_explicit_boolean(offline_client, negative):
  get_client, _, read = offline_client
  with pytest.raises(ToolError, match="negative must be a boolean"):
    campaigns.add_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, ["2840"], negative
    )
  get_client.assert_not_called()
  read.assert_not_called()


@pytest.mark.parametrize("function", ["add", "remove"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_id": "0"},
        {"customer_id": "123/a"},
        {"campaign_id": "0"},
        {"campaign_id": "222 OR 1=1"},
        {"campaign_id": True},
        {"login_customer_id": ""},
        {"login_customer_id": "-1"},
        {"validate_only": "false"},
        {"validate_only": 1},
        {"partial_failure": "true"},
        {"partial_failure": 0},
    ],
)
def test_invalid_shared_inputs_fail_before_client_or_preflight(
    offline_client, function, overrides
):
  get_client, _, read = offline_client
  kwargs = {"customer_id": CUSTOMER_ID, "campaign_id": CAMPAIGN_ID}
  kwargs.update(overrides)
  if function == "add":
    call = campaigns.add_campaign_location_targets
    kwargs.update(geo_target_ids=["2840"], negative=False)
  else:
    call = campaigns.remove_campaign_location_targets
    kwargs.update(criterion_ids=["91"])
  with pytest.raises(ToolError):
    call(**kwargs)
  get_client.assert_not_called()
  read.assert_not_called()


@pytest.mark.parametrize(
    "criterion_ids", [["91", "92"], '["91", "92"]', "91,92"]
)
def test_remove_preflights_all_locations_and_builds_v24_removals(
    offline_client, criterion_ids
):
  get_client, service, read = offline_client
  read.return_value = [
      _row("92", **{"campaign_criterion.status": "PAUSED"}),
      _row("91"),
  ]
  service.mutate_campaign_criteria.return_value = _response(
      [_resource("91"), _resource("92")]
  )

  result = campaigns.remove_campaign_location_targets(
      CUSTOMER_ID, CAMPAIGN_ID, criterion_ids, login_customer_id="9876543210"
  )

  query, customer, manager = read.call_args.args
  assert customer == CUSTOMER_ID and manager == "9876543210"
  assert "campaign.id = 222" in query
  assert "campaign_criterion.criterion_id IN (91, 92)" in query
  assert "LIMIT" not in query.upper()
  assert preprocess_gaql_query(query) == (
      query + " PARAMETERS omit_unselected_resource_names=true"
  )
  get_client.assert_called_once_with(manager)
  request = MutateCampaignCriteriaRequest(
      **service.mutate_campaign_criteria.call_args.kwargs["request"]
  )
  native_request = MutateCampaignCriteriaRequest.pb(request)
  assert [operation.remove for operation in native_request.operations] == [
      _resource("91"),
      _resource("92"),
  ]
  assert all(
      operation.WhichOneof("operation") == "remove"
      for operation in native_request.operations
  )
  assert result["removed_criterion_ids"] == ["91", "92"]
  assert result["mutated_count"] == result["submitted_count"] == 2
  assert result["failures"] == []


@pytest.mark.parametrize(
    "criterion_ids",
    [
        [],
        "",
        ["0"],
        ["-1"],
        [True],
        [1.5],
        [str(2**63)],
        ["91", "091"],
        ["91~92"],
    ],
)
def test_invalid_criterion_ids_never_read_or_mutate(
    offline_client, criterion_ids
):
  get_client, _, read = offline_client
  with pytest.raises(ToolError):
    campaigns.remove_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, criterion_ids
    )
  get_client.assert_not_called()
  read.assert_not_called()


@pytest.mark.parametrize(
    "rows, message",
    [
        ([], "not found"),
        ([_row("91")], "92"),
        (
            [_row("91"), _row("92", **{"campaign_criterion.type": "KEYWORD"})],
            "not a LOCATION",
        ),
        (
            [
                _row("91"),
                _row("92", **{"campaign_criterion.status": "REMOVED"}),
            ],
            "not active",
        ),
        (
            [
                _row("91"),
                _row("92", **{"campaign_criterion.status": "UNKNOWN"}),
            ],
            "not active",
        ),
        (
            [_row("91"), _row("92", **{"campaign.id": "333"})],
            "different account/campaign",
        ),
        (
            [
                _row("91"),
                _row(
                    "92",
                    **{
                        "campaign_criterion.resource_name": _resource(
                            "92", customer_id="555"
                        )
                    },
                ),
            ],
            "different account/campaign",
        ),
        (
            [
                _row("91"),
                _row(
                    "92",
                    **{
                        "campaign_criterion.resource_name": _resource(
                            "92", campaign_id="333"
                        )
                    },
                ),
            ],
            "different account/campaign",
        ),
        ([_row("91"), _row("93")], "different account/campaign"),
    ],
)
def test_removal_rejects_entire_unsafe_batch_even_with_partial_failure(
    offline_client, rows, message
):
  get_client, service, read = offline_client
  read.return_value = rows
  with pytest.raises(ToolError, match=message):
    campaigns.remove_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, ["91", "92"], partial_failure=True
    )
  get_client.assert_not_called()
  service.mutate_campaign_criteria.assert_not_called()


def _call(function, **kwargs):
  if function == "add":
    return campaigns.add_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, ["2840", "2036"], False, **kwargs
    )
  return campaigns.remove_campaign_location_targets(
      CUSTOMER_ID, CAMPAIGN_ID, ["91", "92"], **kwargs
  )


@pytest.mark.parametrize("function", ["add", "remove"])
def test_validation_only_never_reports_mutations(offline_client, function):
  _, service, read = offline_client
  read.return_value = [_row("91"), _row("92")]
  service.mutate_campaign_criteria.return_value = _response()
  result = _call(function, validate_only=True)
  assert (
      service.mutate_campaign_criteria.call_args.kwargs["request"][
          "validate_only"
      ]
      is True
  )
  assert result["validate_only"] is True
  assert result["validation_successful"] is True
  assert result["submitted_count"] == 2
  assert result["mutated_count"] == 0
  assert (
      result["resource_names"]
      == result["successes"]
      == result["failures"]
      == []
  )
  id_key = (
      "created_criterion_ids" if function == "add" else "removed_criterion_ids"
  )
  assert result[id_key] == []


@pytest.mark.parametrize("function", ["add", "remove"])
@pytest.mark.parametrize("failed_index", [0, 1])
def test_partial_failure_maps_native_v24_error_to_exact_operation(
    offline_client, function, failed_index
):
  _, service, read = offline_client
  read.return_value = [_row("91"), _row("92")]
  names = [_resource("91"), _resource("92")]
  names[failed_index] = ""
  service.mutate_campaign_criteria.return_value = _response(
      names, [failed_index]
  )
  result = _call(function, partial_failure=True)
  assert (
      service.mutate_campaign_criteria.call_args.kwargs["request"][
          "partial_failure"
      ]
      is True
  )
  assert result["mutated_count"] == 1
  assert result["submitted_count"] == 2
  success_index = 1 - failed_index
  assert result["resource_names"] == [names[success_index]]
  assert result["successes"][0]["index"] == success_index
  assert result["failures"] == [
      {"reason": "Invalid location", "code": 3, "indexes": [failed_index]}
  ]


@pytest.mark.parametrize("function", ["add", "remove"])
def test_failed_partial_dry_run_reports_errors_without_changes(
    offline_client, function
):
  _, service, read = offline_client
  read.return_value = [_row("91"), _row("92")]
  service.mutate_campaign_criteria.return_value = _response(failed_indexes=[0])
  result = _call(function, validate_only=True, partial_failure=True)
  assert result["validation_successful"] is False
  assert result["mutated_count"] == 0
  assert result["resource_names"] == result["successes"] == []
  assert result["failures"][0]["indexes"] == [0]


def _ads_exception():
  return GoogleAdsException(
      error=mock.Mock(),
      failure=GoogleAdsFailure(
          errors=[GoogleAdsError(message="Internal error")]
      ),
      call=mock.Mock(),
      request_id="offline-test",
  )


@pytest.mark.parametrize("function", ["add", "remove"])
@pytest.mark.parametrize(
    "error",
    [exceptions.ServiceUnavailable("offline outage"), _ads_exception()],
)
def test_mutation_errors_are_centralized_and_never_retried(
    offline_client, function, error
):
  _, service, read = offline_client
  read.return_value = [_row("91"), _row("92")]
  service.mutate_campaign_criteria.side_effect = error
  with pytest.raises(ToolError, match="offline outage|Internal error"):
    _call(function)
  service.mutate_campaign_criteria.assert_called_once()


@pytest.mark.parametrize("function", ["add", "remove"])
def test_result_iteration_is_inside_error_boundary(offline_client, function):
  _, service, read = offline_client
  read.return_value = [_row("91"), _row("92")]

  def broken_results():
    yield mock.Mock(resource_name=_resource("91"))
    raise exceptions.ServiceUnavailable("iteration outage")

  service.mutate_campaign_criteria.return_value = mock.Mock(
      results=broken_results()
  )
  with pytest.raises(ToolError, match="iteration outage"):
    _call(function)
  service.mutate_campaign_criteria.assert_called_once()


def test_preflight_error_is_centralized_without_mutation_client(
    offline_client,
):
  get_client, service, read = offline_client
  read.side_effect = exceptions.ServiceUnavailable("read outage")
  with pytest.raises(ToolError, match="read outage"):
    campaigns.remove_campaign_location_targets(
        CUSTOMER_ID, CAMPAIGN_ID, ["91"]
    )
  get_client.assert_not_called()
  service.mutate_campaign_criteria.assert_not_called()


def test_large_batch_submits_every_operation_and_exports_every_result(
    offline_client, tmp_path
):
  _, service, _ = offline_client
  names = [_resource(str(index + 1)) for index in range(150)]
  service.mutate_campaign_criteria.return_value = _response(names)
  with mock.patch.object(
      api.tempfile, "gettempdir", return_value=str(tmp_path)
  ):
    result = campaigns.add_campaign_location_targets(
        CUSTOMER_ID,
        CAMPAIGN_ID,
        [str(index + 1) for index in range(150)],
        False,
    )
  request = service.mutate_campaign_criteria.call_args.kwargs["request"]
  assert len(request["operations"]) == result["mutated_count"] == 150
  assert result["complete_counts"]["resource_names"] == 150
  assert result["returned_counts"]["resource_names"] < 150
  assert len(json.dumps(result).encode()) <= api.INLINE_RESPONSE_BYTE_LIMIT
  with open(
      result["full_mutation_result_artifact"]["file_path"], encoding="utf-8"
  ) as stream:
    rows = list(csv.DictReader(stream))
  assert len(rows) == 450
  exported = [
      json.loads(row["result"])
      for row in rows
      if row["result_type"] == "resource_names"
  ]
  assert exported == names


def test_tools_keep_session_lock_and_boolean_protocol_validation(
    offline_client,
):
  get_client, _, _ = offline_client

  async def check():
    async with Client(mcp_server) as client:
      tools = {tool.name: tool for tool in await client.list_tools()}
      assert "add_campaign_location_targets" not in tools
      assert "remove_campaign_location_targets" not in tools
      await client.call_tool(docs.unlock_mutation_tools.__name__, {})
      tools = {tool.name: tool for tool in await client.list_tools()}
      for name in (
          "add_campaign_location_targets",
          "remove_campaign_location_targets",
      ):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.idempotentHint is False
      assert (
          tools["remove_campaign_location_targets"].annotations.destructiveHint
          is True
      )
      assert (
          "negative"
          in tools["add_campaign_location_targets"].inputSchema["required"]
      )
      response = await client.call_tool(
          "add_campaign_location_targets",
          {
              "customer_id": CUSTOMER_ID,
              "campaign_id": CAMPAIGN_ID,
              "geo_target_ids": ["2840"],
              "negative": "false",
          },
          raise_on_error=False,
      )
      assert response.is_error is True
      await client.call_tool(docs.lock_mutation_tools.__name__, {})

  asyncio.run(check())
  get_client.assert_not_called()
