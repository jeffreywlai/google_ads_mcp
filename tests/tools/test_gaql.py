# Copyright 2025 Google LLC
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

"""Tests for shared GAQL helper functions."""

from ads_mcp.tools._gaql import quote_enum_values
from ads_mcp.tools._gaql import validate_date_range
from fastmcp.exceptions import ToolError
import pytest


def test_quote_enum_values_normalizes_names():
  assert quote_enum_values(["enabled", "PAUSED"]) == "ENABLED, PAUSED"


def test_quote_enum_values_rejects_malformed_names():
  with pytest.raises(ToolError, match="Invalid enum value"):
    quote_enum_values(["ENABLED) OR campaign.id > 0"])


def test_validate_date_range_normalizes_supported_function():
  assert validate_date_range("last_30_days") == "LAST_30_DAYS"


def test_validate_date_range_rejects_malformed_function():
  with pytest.raises(ToolError, match="Invalid date_range"):
    validate_date_range("LAST_30_DAYS OR metrics.clicks > 0")
