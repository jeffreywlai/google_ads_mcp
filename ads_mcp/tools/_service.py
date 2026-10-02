"""Strict native service request parsing and lossless bounded read results."""

import json
import math
import re
from typing import Any

from fastmcp.exceptions import ToolError
from google.protobuf import descriptor as descriptors
from google.protobuf.field_mask_pb2 import FieldMask
from google.protobuf.json_format import MessageToDict
from google.protobuf.json_format import ParseDict
from google.protobuf.json_format import ParseError
from google.protobuf.message import Message as ProtobufMessage
import proto

from ads_mcp.tools.api import INLINE_PAGE_BYTE_LIMIT
from ads_mcp.tools.api import finalize_bounded_response


def normalized_account_id(value: Any, field_name: str = "customer_id") -> str:
  """Validates account IDs, allowing the familiar dashes/spaces formatting."""
  if isinstance(value, bool) or not isinstance(value, (int, str)):
    raise ToolError(f"{field_name} must be a positive numeric account ID.")
  try:
    text = str(value).strip()
  except ValueError as exc:
    raise ToolError(f"{field_name} is too large to be an account ID.") from exc
  if len(text) > 40:
    raise ToolError(f"{field_name} is too large to be an account ID.")
  if not re.fullmatch(r"[0-9]+(?:[ -]+[0-9]+)*", text):
    raise ToolError(
        f"{field_name} must be numeric; dashes/spaces are allowed."
    )
  value = int(re.sub(r"[ -]", "", text))
  if value <= 0:
    raise ToolError(f"{field_name} must be greater than zero.")
  return str(value)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
  result = {}
  for key, value in pairs:
    if key in result:
      raise ValueError(f"Duplicate request field: {key}.")
    result[key] = value
  return result


def _invalid_json_constant(value: str) -> None:
  raise ValueError(f"Non-JSON numeric literal: {value}.")


def _request_object(request: dict[str, Any] | str) -> dict[str, Any]:
  if isinstance(request, str):
    try:
      request = json.loads(
          request,
          object_pairs_hook=_unique_json_object,
          parse_constant=_invalid_json_constant,
      )
    except (ValueError, RecursionError) as exc:
      raise ToolError(f"request must be a valid JSON object: {exc}") from exc
  if not isinstance(request, dict):
    raise ToolError("request must be an object or JSON object string.")
  return request


def _native_json_fields(value: Any, descriptor: Any) -> Any:
  """Rejects repeated field aliases and accepts native FieldMask paths."""
  if descriptor.full_name == "google.protobuf.FieldMask" and isinstance(
      value, dict
  ):
    paths = value.get("paths")
    if (
        set(value) != {"paths"}
        or not isinstance(paths, list)
        or not all(isinstance(path, str) for path in paths)
    ):
      raise ValueError(
          "FieldMask objects require only a paths array of strings."
      )
    return FieldMask(paths=paths).ToJsonString()
  if not isinstance(value, dict):
    return value
  result = {}
  seen = set()
  aliases = {field.json_name: field for field in descriptor.fields}
  for key, item in value.items():
    if not isinstance(key, str):
      raise ValueError("Request object field names must be strings.")
    field = descriptor.fields_by_name.get(key) or aliases.get(key)
    if field is None:
      result[key] = item
      continue
    if field.name in seen:
      raise ValueError(f"Duplicate field aliases for {field.name}.")
    seen.add(field.name)
    if field.message_type is not None:
      if field.message_type.GetOptions().map_entry and isinstance(item, dict):
        map_value = field.message_type.fields_by_name["value"]
        if map_value.message_type:
          item = {
              map_key: _native_json_fields(map_item, map_value.message_type)
              for map_key, map_item in item.items()
          }
        elif map_value.type != descriptors.FieldDescriptor.TYPE_BOOL:
          if any(isinstance(entry, bool) for entry in item.values()):
            raise ValueError(f"Boolean values are invalid for {field.name}.")
      elif field.is_repeated:
        if isinstance(item, list):
          item = [
              _native_json_fields(entry, field.message_type) for entry in item
          ]
      else:
        item = _native_json_fields(item, field.message_type)
    elif field.type != descriptors.FieldDescriptor.TYPE_BOOL:
      values = item if field.is_repeated and isinstance(item, list) else [item]
      if any(isinstance(entry, bool) for entry in values):
        raise ValueError(f"Boolean values are invalid for {field.name}.")
    result[key] = item
  return result


def _finite_message_numbers(
    message: ProtobufMessage, path: str = "request"
) -> None:
  """Rejects protobuf JSON nonfinite strings in numeric fields recursively."""
  for field, value in message.ListFields():
    field_path = f"{path}.{field.name}"
    if field.message_type and field.message_type.GetOptions().map_entry:
      value_field = field.message_type.fields_by_name["value"]
      values = value.values()
    else:
      value_field = field
      values = value if field.is_repeated else [value]
    for item in values:
      if value_field.message_type:
        _finite_message_numbers(item, field_path)
      elif value_field.type in (
          descriptors.FieldDescriptor.TYPE_DOUBLE,
          descriptors.FieldDescriptor.TYPE_FLOAT,
      ) and not math.isfinite(item):
        raise ValueError(f"Non-finite numeric value at {field_path}.")


def parse_service_request(
    request: dict[str, Any] | str, request_type: type[proto.Message]
) -> proto.Message:
  """Parses typed protobuf JSON before creating a Google Ads client.

  Snake-case and protobuf camelCase names are accepted, but using both names
  for one field is rejected. Unknown nested fields, invalid types/enums, and
  conflicting oneof choices are rejected. FieldMask accepts its protobuf JSON
  string or a native {paths: [snake_case.path]} object. Nonfinite numeric
  values are rejected. Required fields, resource ownership, update-mask scope,
  and business rules remain caller/API checks; protobuf type validation does
  not imply request eligibility.
  """
  value = _request_object(request)
  native = request_type.pb(request_type())
  try:
    value = _native_json_fields(value, native.DESCRIPTOR)
    ParseDict(value, native, ignore_unknown_fields=False)
    _finite_message_numbers(native)
  except (
      ParseError,
      ValueError,
      TypeError,
      OverflowError,
      RecursionError,
  ) as exc:
    raise ToolError(f"Invalid {request_type.__name__} request: {exc}") from exc
  return request_type.wrap(native)


def scoped_service_request(
    customer_id: str,
    request: dict[str, Any] | str,
    request_type: type[proto.Message],
) -> tuple[str, proto.Message]:
  """Pins a native customer_id field without fabricating account-less scope."""
  customer_id = normalized_account_id(customer_id)
  value = dict(_request_object(request))
  if "customer_id" in request_type.pb().DESCRIPTOR.fields_by_name:
    supplied = [key for key in ("customer_id", "customerId") if key in value]
    if len(supplied) > 1:
      raise ToolError("Duplicate field aliases for customer_id.")
    if supplied:
      supplied_id = normalized_account_id(
          value.pop(supplied[0]), "request.customer_id"
      )
      if supplied_id != customer_id:
        raise ToolError("request.customer_id must match customer_id.")
    value["customer_id"] = customer_id
  return customer_id, parse_service_request(value, request_type)


def service_response_dict(response: Any) -> dict[str, Any]:
  """Preserves protobuf presence, omitting ambiguous non-presence defaults.

  Present optional zeros remain zero. A proto3 scalar without presence cannot
  prove that its default was supplied, so canonical JSON omits it instead of
  interpreting an unsupported metric as zero.
  """
  if isinstance(response, proto.Message):
    response = proto.Message.pb(response)
  if not isinstance(response, ProtobufMessage):
    raise ToolError("Expected a typed Google Ads service response.")
  return MessageToDict(response, preserving_proto_field_name=True)


def bounded_service_response(
    response: Any,
    *,
    customer_id: str,
    service_name: str,
    method_name: str,
    account_scoped: bool = True,
) -> dict[str, Any]:
  """Bounds a complete service result without implicit exported file writes."""
  result = {
      "customer_id": customer_id,
      "service": service_name,
      "method": method_name,
      "account_scope": "request.customer_id"
      if account_scoped
      else "global_metadata",
      "response": service_response_dict(response),
      "response_scope": (
          "Every field provided by this service response. Absent metrics may "
          "be unavailable or not requested; no missing values are synthesized."
      ),
      "complete_inline": False,
      "truncated": False,
  }
  result = finalize_bounded_response(
      result, ("response",), max_bytes=INLINE_PAGE_BYTE_LIMIT
  )
  result["complete_inline"] = not result["truncated"]
  return result


def request_schema(request_type: type[proto.Message]) -> dict[str, Any]:
  """Describes installed native fields/enums without guessing service rules."""
  definitions = {}

  def message_schema(descriptor):
    name = descriptor.full_name
    if name not in definitions:
      definitions[name] = {}
      if name == "google.protobuf.FieldMask":
        definitions[name] = {
            "anyOf": [
                {"type": "string"},
                {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["paths"],
                    "additionalProperties": False,
                },
            ]
        }
      else:
        properties = {}
        aliases = {}
        for field in descriptor.fields:
          properties[field.name] = field_schema(field)
          if field.json_name != field.name:
            properties[field.json_name] = properties[field.name]
            aliases[field.json_name] = field.name
        definitions[name] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
            "property_aliases": aliases,
            "oneof_groups": {
                group.name: [field.name for field in group.fields]
                for group in descriptor.oneofs
                if len(group.fields) > 1
            },
        }
    return {"$ref": f"#/$defs/{name}"}

  def field_schema(field):
    if field.message_type:
      schema = message_schema(field.message_type)
    elif field.enum_type:
      schema = {
          "anyOf": [
              {
                  "type": "string",
                  "enum": [item.name for item in field.enum_type.values],
              },
              {"type": "integer"},
          ]
      }
    elif field.type == descriptors.FieldDescriptor.TYPE_BOOL:
      schema = {"type": "boolean"}
    elif field.type in (
        descriptors.FieldDescriptor.TYPE_STRING,
        descriptors.FieldDescriptor.TYPE_BYTES,
    ):
      schema = {"type": "string"}
    elif field.type in (
        descriptors.FieldDescriptor.TYPE_DOUBLE,
        descriptors.FieldDescriptor.TYPE_FLOAT,
    ):
      schema = {"type": "number"}
    else:
      schema = {
          "anyOf": [
              {"type": "integer"},
              {"type": "string", "pattern": "^-?[0-9]+$"},
          ]
      }
    if field.message_type and field.message_type.GetOptions().map_entry:
      value_field = field.message_type.fields_by_name["value"]
      return {
          "type": "object",
          "additionalProperties": field_schema(value_field),
      }
    if field.is_repeated:
      return {"type": "array", "items": schema}
    return schema

  root = message_schema(request_type.pb().DESCRIPTOR)
  return {
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      **root,
      "$defs": definitions,
      "protobuf_json_notes": (
          "Shows snake_case fields; protobuf camelCase aliases are accepted. "
          "Only one member of each oneof_group may be set. Google enforces "
          "required inputs, combinations, resource eligibility, and access."
      ),
  }
