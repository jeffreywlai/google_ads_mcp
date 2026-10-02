"""Strict native service parsing protects client and mutation boundaries."""

from fastmcp.exceptions import ToolError
from google.ads.googleads.v25.services.types import campaign_service
from google.ads.googleads.v25.services.types import content_creator_insights_service
import jsonschema
import proto
import pytest

from ads_mcp.tools import _service


def test_native_field_mask_paths_and_json_mask_have_equal_typed_meaning():
  operation = {
      "update": {
          "resource_name": "customers/123/campaigns/4",
          "target_roas": {"target_roas": 2.0},
      }
  }
  native = _service.parse_service_request(
      {
          "customer_id": "123",
          "operations": [
              {
                  **operation,
                  "update_mask": {"paths": ["target_roas.target_roas"]},
              }
          ],
      },
      campaign_service.MutateCampaignsRequest,
  )
  as_json = _service.parse_service_request(
      {
          "customerId": "123",
          "operations": [{**operation, "updateMask": "targetRoas.targetRoas"}],
      },
      campaign_service.MutateCampaignsRequest,
  )
  assert native == as_json
  first_operation = list(native.operations)[0]
  assert list(first_operation.update_mask.paths) == ["target_roas.target_roas"]
  assert first_operation.update.target_roas.target_roas == 2
  jsonschema.validate(
      {
          "operations": [
              {
                  **operation,
                  "update_mask": {"paths": ["target_roas.target_roas"]},
              }
          ]
      },
      _service.request_schema(campaign_service.MutateCampaignsRequest),
  )


@pytest.mark.parametrize(
    "payload",
    [
        {"operations": [{"create": {}, "update": {}}]},
        {"operations": [{"update": {"bad_field": True}}]},
        {"validate_only": "false"},
        {"validate_only": True, "validateOnly": False},
        {"operations": [{"update_mask": {"paths": ["name"], "bad": 1}}]},
        {"operations": [{"update_mask": {"paths": "name"}}]},
        {"operations": [{"update_mask": {"paths": [1]}}]},
        {"operations": [{"update_mask": {"paths": ["targetRoas"]}}]},
        {
            "operations": [
                {
                    "update": {
                        "name": "one",
                        "resourceName": "customers/123/campaigns/4",
                        "resource_name": "customers/123/campaigns/4",
                    }
                }
            ]
        },
    ],
)
def test_strict_nested_masks_oneofs_booleans_aliases(payload):
  with pytest.raises(ToolError):
    _service.parse_service_request(
        payload, campaign_service.MutateCampaignsRequest
    )


def test_matching_account_alias_normalizes_without_mutating_input():
  request = {
      "customerId": "12-3",
      "search_channels": {"youtube_channel_handles": ["@example"]},
  }
  customer_id, native = _service.scoped_service_request(
      "12 3",
      request,
      content_creator_insights_service.GenerateCreatorInsightsRequest,
  )
  assert customer_id == native.customer_id == "123"
  assert request["customerId"] == "12-3"


def test_unset_optional_response_fields_are_not_synthesized():
  result = _service.service_response_dict(
      content_creator_insights_service.GenerateCreatorInsightsResponse()
  )
  assert result == {}


def test_empty_request_is_type_valid_but_does_not_claim_api_eligibility():
  parsed = _service.parse_service_request(
      {}, campaign_service.MutateCampaignsRequest
  )
  assert not parsed.operations
  assert (
      "Google enforces"
      in _service.request_schema(campaign_service.MutateCampaignsRequest)[
          "protobuf_json_notes"
      ]
  )


@pytest.mark.parametrize(
    "number",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        "NaN",
        "Infinity",
        "-Infinity",
        True,
    ],
)
def test_nonfinite_and_boolean_nested_operation_numbers_are_rejected(number):
  with pytest.raises(ToolError):
    _service.parse_service_request(
        {"operations": [{"update": {"target_roas": {"target_roas": number}}}]},
        campaign_service.MutateCampaignsRequest,
    )


class _NumericRequest(proto.Message):
  numbers = proto.RepeatedField(proto.DOUBLE, number=1)
  mapping = proto.MapField(proto.STRING, proto.DOUBLE, number=2)
  label = proto.Field(proto.STRING, number=3)


@pytest.mark.parametrize(
    "payload",
    [
        {"numbers": [1, "NaN"]},
        {"mapping": {"item": "Infinity"}},
        {"numbers": [True]},
        {"mapping": {"item": False}},
    ],
)
def test_invalid_repeated_and_map_numbers_are_rejected(payload):
  with pytest.raises(ToolError):
    _service.parse_service_request(payload, _NumericRequest)


def test_finite_zero_negative_numbers_and_text_nan_remain_valid():
  native = _service.parse_service_request(
      {"numbers": [0, -1.5], "mapping": {"item": 2.0}, "label": "NaN"},
      _NumericRequest,
  )
  assert list(native.numbers) == [0, -1.5]
  assert native.label == "NaN"
