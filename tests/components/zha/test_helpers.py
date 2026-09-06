"""Tests for ZHA helpers."""

import enum
import logging
from typing import Any, cast
from unittest.mock import patch

from probatio import to_field_list
import pytest
from syrupy.assertion import SnapshotAssertion
from zigpy.application import ControllerApplication
import zigpy.types
from zigpy.types.basic import uint16_t
from zigpy.zcl.clusters import general, homeautomation, lighting, security

from homeassistant.components.zha import const as zha_const
from homeassistant.components.zha.helpers import (
    attribute_type_to_probatio_schema,
    attribute_value_to_form_value,
    cluster_command_schema_to_probatio_schema,
    convert_to_zcl_values,
    create_zha_config,
    exclude_none_values,
    form_value_to_attribute_value,
    get_zha_data,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component

from tests.common import MockConfigEntry

_LOGGER = logging.getLogger(__name__)


class _TestFlags(zigpy.types.bitmap8):
    """Test flag type used for value conversion checks."""

    Option_A = 1
    Option_B = 2


class _TestEnum(enum.Enum):
    """Test enum type used for value conversion checks."""

    Option_A = 1
    Option_B = 2


class _TestStruct(zigpy.types.Struct):
    """Test struct type used for value conversion checks."""

    field_a: zigpy.types.uint8_t
    field_b: zigpy.types.uint16_t


class _TestOptionalStruct(zigpy.types.Struct):
    """Struct with an optional field for service value conversion."""

    required: zigpy.types.uint8_t
    optional: zigpy.types.uint16_t = zigpy.types.StructField(optional=True)


class _LargeIntegerStruct(zigpy.types.Struct):
    """Struct containing integer fields that need exact text input."""

    signed: zigpy.types.int64s
    unsigned: zigpy.types.LVList[zigpy.types.uint64_t]


class _TestList(list):
    """Test list-like type used for value conversion checks."""

    _item_type = zigpy.types.uint8_t


class _UntypedList(list):
    """List-like test type without a concrete item type."""

    _item_type = "untyped"


class _TestFixedLengthList(list):
    """Test fixed-length list-like type for strict list validation checks."""

    _item_type = zigpy.types.uint8_t
    _length = 2


class _SelectorNestedStruct(zigpy.types.Struct):
    """Nested struct for selector-shape coverage."""

    nested_field: zigpy.types.uint8_t


class _UnsupportedSelectorType:
    """Type that should fall back to text selector handling."""


class _SelectorCoverageStruct(zigpy.types.Struct):
    """Struct exercising selector branch coverage."""

    bool_field: zigpy.types.Bool
    flag_field: _TestFlags
    enum_field: _TestEnum
    struct_field: _SelectorNestedStruct
    int_field: zigpy.types.uint8_t
    float_field: zigpy.types.Single
    text_field: zigpy.types.CharacterString
    list_field: _TestList
    fallback_field: _UnsupportedSelectorType


def test_zcl_schema_conversions(snapshot: SnapshotAssertion) -> None:
    """Test ZHA ZCL schema conversion helpers."""
    command_schema = lighting.Color.ServerCommandDefs.color_loop_set.schema
    form_fields = to_field_list(
        cluster_command_schema_to_probatio_schema(command_schema),
        custom_serializer=cv.custom_serializer,
    )
    assert form_fields == snapshot


@pytest.mark.parametrize(
    ("flags", "action", "direction", "expected_flags"),
    [
        pytest.param(
            ["Action", "Start Hue"],
            "Activate from current hue",
            "Increment",
            9,
            id="labels",
        ),
        pytest.param([1, 8], 2, 1, 9, id="numeric"),
        pytest.param([], "Activate from current hue", "Increment", 0, id="empty_flags"),
    ],
)
def test_zcl_value_conversions(
    flags: list[str | int], action: str | int, direction: str | int, expected_flags: int
) -> None:
    """Test command conversion preserves form labels and existing numeric inputs."""
    converted_data = convert_to_zcl_values(
        {
            "update_flags": flags,
            "action": action,
            "direction": direction,
            "time": 20,
            "start_hue": 196,
        },
        lighting.Color.ServerCommandDefs.color_loop_set.schema,
    )
    assert isinstance(
        converted_data["update_flags"], lighting.Color.ColorLoopUpdateFlags
    )
    assert converted_data["update_flags"] == expected_flags
    assert (
        converted_data["action"]
        is lighting.Color.ColorLoopAction.Activate_from_current_hue
    )
    assert converted_data["direction"] is lighting.Color.ColorLoopDirection.Increment
    assert isinstance(converted_data["time"], uint16_t)
    assert converted_data["time"] == 20
    assert isinstance(converted_data["start_hue"], uint16_t)
    assert converted_data["start_hue"] == 196


@pytest.mark.parametrize(
    "attr_type",
    [
        pytest.param(zigpy.types.Bool, id="bool"),
        pytest.param(_TestFlags, id="flags"),
        pytest.param(_TestEnum, id="enum"),
        pytest.param(zigpy.types.uint8_t, id="integer"),
        pytest.param(zigpy.types.uint64_t, id="large_integer"),
        pytest.param(_LargeIntegerStruct, id="nested_large_integer"),
        pytest.param(zigpy.types.Single, id="float"),
        pytest.param(bytes, id="bytes"),
        pytest.param(zigpy.types.EUI64, id="eui64"),
        pytest.param(zigpy.types.KeyData, id="key_data"),
        pytest.param(_TestStruct, id="struct"),
        pytest.param(_TestList, id="list"),
        pytest.param(_SelectorCoverageStruct, id="nested_struct"),
    ],
)
def test_attribute_type_to_probatio_schema_shapes(
    attr_type: type, snapshot: SnapshotAssertion
) -> None:
    """Test serialized selectors for attribute types and nested fields."""
    assert (
        to_field_list(
            attribute_type_to_probatio_schema(attr_type),
            custom_serializer=cv.custom_serializer,
        )
        == snapshot
    )


def test_attribute_type_to_probatio_schema_non_type_falls_back_to_text() -> None:
    """Test non-type schema inputs safely fall back to text values."""
    assert to_field_list(
        attribute_type_to_probatio_schema(cast(Any, 123)),
        custom_serializer=cv.custom_serializer,
    ) == [{"type": "string", "name": "value", "required": True}]


@pytest.mark.parametrize(
    ("attr_type", "raw_value", "expected_value"),
    [
        pytest.param(zigpy.types.Bool, "true", zigpy.types.Bool.true, id="bool_text"),
        pytest.param(
            zigpy.types.Bool,
            "Bool.false",
            zigpy.types.Bool.false,
            id="qualified_bool_text",
        ),
        pytest.param(
            _TestFlags, 3, _TestFlags.Option_A | _TestFlags.Option_B, id="flag_integer"
        ),
        pytest.param(
            _TestFlags,
            "03",
            _TestFlags.Option_A | _TestFlags.Option_B,
            id="flag_numeric_text",
        ),
        pytest.param(
            _TestFlags,
            "Option_A | Option_B",
            _TestFlags.Option_A | _TestFlags.Option_B,
            id="flag_pipe_text",
        ),
        pytest.param(
            _TestFlags,
            ["Option A", 2],
            _TestFlags.Option_A | _TestFlags.Option_B,
            id="mixed_flag_list",
        ),
        pytest.param(
            zigpy.types.uint8_t, "12", zigpy.types.uint8_t(12), id="integer_text"
        ),
        pytest.param(
            zigpy.types.uint8_t, "0x10", zigpy.types.uint8_t(16), id="integer_hex_text"
        ),
        pytest.param(
            zigpy.types.uint8_t, True, zigpy.types.uint8_t(1), id="integer_bool"
        ),
        pytest.param(
            zigpy.types.Single, "2.75", zigpy.types.Single(2.75), id="float_text"
        ),
        pytest.param(
            zigpy.types.SerializableBytes,
            "b'abc'",
            zigpy.types.SerializableBytes(b"abc"),
            id="bytes_literal",
        ),
    ],
)
def test_form_value_to_attribute_value_legacy_input(
    attr_type: type, raw_value: Any, expected_value: object
) -> None:
    """Test typed forms preserve scalar inputs accepted by existing services."""
    converted = form_value_to_attribute_value(raw_value, attr_type)
    assert isinstance(converted, attr_type)
    assert converted == expected_value


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [
        (["Option A", "Option B"], _TestFlags.Option_A | _TestFlags.Option_B),
    ],
)
def test_form_value_to_attribute_value_flag_inputs(
    raw_value: Any, expected_value: _TestFlags
) -> None:
    """Test flag conversion consumes multi-select label lists."""
    assert form_value_to_attribute_value(raw_value, _TestFlags) == expected_value


@pytest.mark.parametrize(
    ("attr_type", "raw_value", "error"),
    [
        pytest.param(
            _TestFlags,
            ["Not A Real Flag"],
            "Invalid value",
            id="flag_invalid_member",
        ),
        pytest.param(
            _TestEnum, "Not an option", "Invalid value", id="enum_invalid_member"
        ),
        pytest.param(_TestEnum, 999, "Invalid value", id="enum_invalid_numeric_member"),
        pytest.param(bytes, "zz-not-hex", "Invalid value", id="bytes_invalid_hex"),
        pytest.param(
            zigpy.types.uint8_t,
            "not-an-int",
            "Invalid value",
            id="integer_invalid_inputs",
        ),
        pytest.param(
            zigpy.types.uint8_t,
            1.2,
            "Invalid integer value",
            id="integer_fractional_float_rejected",
        ),
        pytest.param(
            zigpy.types.uint8_t,
            256,
            "Invalid value",
            id="integer_out_of_range_rejected",
        ),
        pytest.param(
            _TestStruct,
            {"field_a": "invalid", "field_b": 2},
            "Invalid value.*at 'field_a'",
            id="struct_invalid_nested_inputs",
        ),
        pytest.param(
            _TestStruct,
            {"field_a": 1, "field_b": 2, "extra": 3},
            "not a valid option at 'extra'",
            id="struct_unexpected_field",
        ),
        pytest.param(
            _TestStruct,
            {"field_a": 1},
            "required key not provided at 'field_b'",
            id="struct_missing_required_field",
        ),
        pytest.param(
            _TestStruct,
            "invalid",
            "expected a mapping",
            id="struct_invalid_payload_type",
        ),
        pytest.param(
            _TestList,
            [{"not_value": 1}],
            "include a 'value' field",
            id="list_object_missing_value",
        ),
        pytest.param(
            _TestList,
            "bad",
            "List-like attributes require a list/tuple",
            id="list_invalid_payload_type",
        ),
        pytest.param(
            _TestFixedLengthList,
            [{"value": 1}, {"value": 2}, {"value": 3}],
            "requires exactly 2 item",
            id="fixed_length_list_invalid_size",
        ),
        pytest.param(
            zigpy.types.EUI64,
            123,
            "List-like attributes require",
            id="eui64_invalid_payload_type",
        ),
        pytest.param(
            zigpy.types.KeyData,
            123,
            "List-like attributes require",
            id="keydata_invalid_payload_type",
        ),
    ],
)
def test_form_value_to_attribute_value_invalid_input(
    attr_type: type, raw_value: Any, error: str
) -> None:
    """Test invalid form input is rejected with a useful error."""
    with pytest.raises(ValueError, match=error):
        form_value_to_attribute_value(raw_value, attr_type)


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [(False, False), (True, True)],
)
def test_form_value_to_attribute_value_bool_inputs(
    raw_value: Any, expected_value: bool
) -> None:
    """Test bool conversion consumes strict boolean values."""
    value = form_value_to_attribute_value(raw_value, zigpy.types.Bool)
    assert isinstance(value, zigpy.types.Bool)
    assert value == expected_value


def test_form_value_to_attribute_value_enum_inputs() -> None:
    """Test enum conversion supports form labels."""
    assert form_value_to_attribute_value("Option A", _TestEnum) is _TestEnum.Option_A
    assert form_value_to_attribute_value("Option B", _TestEnum) is _TestEnum.Option_B


def test_form_value_to_attribute_value_bytes_inputs() -> None:
    """Test bytes conversion consumes hex strings."""
    assert form_value_to_attribute_value("0102ff", bytes) == b"\x01\x02\xff"


def test_form_value_to_attribute_value_serializable_bytes_inputs() -> None:
    """Test serializable bytes conversion consumes hex strings."""
    converted = form_value_to_attribute_value("0102ff", zigpy.types.SerializableBytes)
    assert isinstance(converted, zigpy.types.SerializableBytes)
    assert converted.value == b"\x01\x02\xff"


@pytest.mark.parametrize("raw_value", ["b'\\x01\\x02\\xff'", "'0102'", "b'abc"])
def test_form_value_to_attribute_value_bytes_literal_rejected(raw_value: str) -> None:
    """Test bytes-literal text is rejected for byte-like values."""
    with pytest.raises(ValueError, match="Invalid value"):
        form_value_to_attribute_value(raw_value, bytes)


def test_form_value_to_attribute_value_float_inputs() -> None:
    """Test float conversion consumes numeric values."""
    value_from_float = form_value_to_attribute_value(1.25, zigpy.types.Single)
    assert isinstance(value_from_float, zigpy.types.Single)
    assert float(value_from_float) == pytest.approx(1.25)

    value_from_int = form_value_to_attribute_value(2, zigpy.types.Single)
    assert isinstance(value_from_int, zigpy.types.Single)
    assert float(value_from_int) == pytest.approx(2.0)


def test_form_value_to_attribute_value_float_constructor_error() -> None:
    """Test float conversion normalizes constructor errors."""
    with (
        patch.object(zigpy.types.Single, "__new__", side_effect=ValueError("boom")),
        pytest.raises(ValueError, match="Invalid value"),
    ):
        form_value_to_attribute_value(1.25, zigpy.types.Single)


def test_form_value_to_attribute_value_integer_integral_float_input() -> None:
    """Test integer conversion accepts integral float payloads."""
    converted = form_value_to_attribute_value(1.0, zigpy.types.uint8_t)
    assert isinstance(converted, zigpy.types.uint8_t)
    assert converted == 1


def test_form_value_to_attribute_value_struct_inputs() -> None:
    """Test struct conversion consumes object-form values."""
    value = form_value_to_attribute_value({"field_a": 1, "field_b": 2}, _TestStruct)
    assert isinstance(value, _TestStruct)
    assert value.field_a == 1
    assert value.field_b == 2


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"required": "0x12"}, b"\x12", id="omitted"),
        pytest.param(
            {"required": "0x12", "optional": 2}, b"\x12\x02\x00", id="provided"
        ),
        pytest.param({"required": "0x12", "optional": None}, b"\x12", id="none"),
    ],
)
def test_form_value_to_attribute_value_optional_struct_fields(
    payload: dict[str, int | str | None], expected: bytes
) -> None:
    """Preserve optional fields and legacy input through struct validation."""
    converted = form_value_to_attribute_value(payload, _TestOptionalStruct)
    assert isinstance(converted, _TestOptionalStruct)
    assert converted.serialize() == expected


def test_form_value_to_attribute_value_struct_passthrough_instance() -> None:
    """Test struct values already typed are accepted as-is."""
    value = _TestStruct(field_a=1, field_b=2)
    assert form_value_to_attribute_value(value, _TestStruct) is value


def test_form_value_to_attribute_value_list_inputs() -> None:
    """Test list-like conversion consumes list-form values."""
    value = form_value_to_attribute_value([1, 2, 3], _TestList)
    assert isinstance(value, _TestList)
    assert value == [1, 2, 3]


def test_form_value_to_attribute_value_list_object_inputs() -> None:
    """Test list-like conversion consumes strict object-list form payloads."""
    value = form_value_to_attribute_value(
        [{"value": 1}, {"value": 2}, {"value": 3}], _TestList
    )
    assert isinstance(value, _TestList)
    assert value == [1, 2, 3]


def test_form_value_to_attribute_value_list_passthrough_instance() -> None:
    """Test list-like values already typed are accepted as-is."""
    value = _TestList([1, 2, 3])
    assert form_value_to_attribute_value(value, _TestList) is value


def test_form_value_to_attribute_value_eui64_inputs() -> None:
    """Test EUI64 conversion accepts colon-delimited text."""
    value = form_value_to_attribute_value("01:02:03:04:05:06:07:08", zigpy.types.EUI64)
    assert isinstance(value, zigpy.types.EUI64)
    assert str(value) == "01:02:03:04:05:06:07:08"


def test_form_value_to_attribute_value_eui64_passthrough_instance() -> None:
    """Test EUI64 values already typed are accepted as-is."""
    value = zigpy.types.EUI64.convert("01:02:03:04:05:06:07:08")
    assert form_value_to_attribute_value(value, zigpy.types.EUI64) is value


def test_form_value_to_attribute_value_keydata_inputs() -> None:
    """Test KeyData conversion accepts compact hex text."""
    value = form_value_to_attribute_value(
        "000102030405060708090a0b0c0d0e0f", zigpy.types.KeyData
    )
    assert isinstance(value, zigpy.types.KeyData)
    assert str(value) == "00:01:02:03:04:05:06:07:08:09:0a:0b:0c:0d:0e:0f"


def test_form_value_to_attribute_value_keydata_passthrough_instance() -> None:
    """Test KeyData values already typed are accepted as-is."""
    value = zigpy.types.KeyData.convert("000102030405060708090a0b0c0d0e0f")
    assert form_value_to_attribute_value(value, zigpy.types.KeyData) is value


def test_form_value_to_attribute_value_none_passthrough() -> None:
    """Test `None` payloads pass through unchanged."""
    assert form_value_to_attribute_value(None, zigpy.types.uint8_t) is None


def test_form_value_to_attribute_value_passthrough_unknown_type() -> None:
    """Test unknown target types pass values through unchanged."""
    payload = {"value": "unchanged"}
    assert form_value_to_attribute_value(payload, _UnsupportedSelectorType) is payload


def test_form_value_to_attribute_value_real_cluster_security_flag_inputs() -> None:
    """Test IAS Zone flag conversion works with real cluster attribute types."""
    zone_status_type = security.IasZone.AttributeDefs.zone_status.type
    converted = form_value_to_attribute_value(
        ["Alarm 1", "Tamper"],
        zone_status_type,
    )
    assert isinstance(converted, zone_status_type)
    assert converted == zone_status_type.Alarm_1 | zone_status_type.Tamper


def test_form_value_to_attribute_value_real_cluster_ota_enum_inputs() -> None:
    """Test OTA enum conversion works with real cluster attribute types."""
    image_upgrade_status_type = general.Ota.AttributeDefs.image_upgrade_status.type
    converted = form_value_to_attribute_value(
        "Download complete",
        image_upgrade_status_type,
    )
    assert isinstance(converted, image_upgrade_status_type)
    assert converted is image_upgrade_status_type.Download_complete


def test_form_value_to_attribute_value_real_cluster_ota_eui64_inputs() -> None:
    """Test OTA EUI64 conversion works with real cluster attribute types."""
    upgrade_server_id_type = general.Ota.AttributeDefs.upgrade_server_id.type
    converted = form_value_to_attribute_value(
        "01:02:03:04:05:06:07:08",
        upgrade_server_id_type,
    )
    assert isinstance(converted, upgrade_server_id_type)
    assert str(converted) == "01:02:03:04:05:06:07:08"


def test_form_value_to_attribute_value_real_cluster_basic_lvbytes_inputs() -> None:
    """Test Basic cluster LVBytes conversion uses real attribute defs."""
    product_code_type = general.Basic.AttributeDefs.product_code.type
    converted = form_value_to_attribute_value("0102ff", product_code_type)
    assert isinstance(converted, product_code_type)
    assert bytes(converted) == b"\x01\x02\xff"


def test_attribute_value_to_form_value_real_cluster_security_flag_inputs() -> None:
    """Test IAS Zone flag values convert to label lists with real attr defs."""
    zone_status_type = security.IasZone.AttributeDefs.zone_status.type
    value = zone_status_type.Alarm_1 | zone_status_type.Tamper
    assert sorted(
        cast(list[str], attribute_value_to_form_value(value, zone_status_type))
    ) == [
        "Alarm 1",
        "Tamper",
    ]


def test_attribute_value_to_form_value_flag_int_inputs() -> None:
    """Test integer-backed flag reads are converted to selected option labels."""
    assert sorted(cast(list[str], attribute_value_to_form_value(3, _TestFlags))) == [
        "Option A",
        "Option B",
    ]


@pytest.mark.parametrize("raw_value", [0, 4, "bad"])
def test_attribute_value_to_form_value_flag_invalid_or_empty_inputs(
    raw_value: Any,
) -> None:
    """Test unsupported integer/text flag values map to no selected options."""
    assert attribute_value_to_form_value(raw_value, _TestFlags) == []


def test_attribute_value_to_form_value_real_cluster_ota_enum_inputs() -> None:
    """Test OTA enum values convert to labels with real attr defs."""
    image_upgrade_status_type = general.Ota.AttributeDefs.image_upgrade_status.type
    value = image_upgrade_status_type.Download_complete
    assert (
        attribute_value_to_form_value(value, image_upgrade_status_type)
        == "Download complete"
    )


def test_attribute_value_to_form_value_enum_inputs() -> None:
    """Test enum values are converted to form labels."""
    assert attribute_value_to_form_value(_TestEnum.Option_A, _TestEnum) == "Option A"
    assert attribute_value_to_form_value(2, _TestEnum) == "2"


@pytest.mark.parametrize("attr_type", [zigpy.types.enum8, zigpy.types.bitmap8])
@pytest.mark.parametrize("value", [0, 1, 255])
def test_unnamed_enum_and_bitmap_round_trip(attr_type: type, value: int) -> None:
    """Types without named members use numeric form values in both directions."""
    form_value = attribute_value_to_form_value(attr_type(value), attr_type)
    assert form_value == value
    assert isinstance(form_value, int)
    validated = attribute_type_to_probatio_schema(attr_type)({"value": form_value})
    converted = form_value_to_attribute_value(validated["value"], attr_type)
    assert isinstance(converted, attr_type)
    assert converted == value


@pytest.mark.parametrize(
    ("attr_type", "value"),
    [
        pytest.param(
            homeautomation.ApplianceIdentification.AttributeDefs.basic_identification.type,
            2**53 + 1,
            id="appliance_identification",
        ),
        pytest.param(zigpy.types.uint64_t, 2**64 - 1, id="uint64_max"),
        pytest.param(zigpy.types.int64s, -(2**63) + 1, id="int64_negative"),
        pytest.param(zigpy.types.int64s, 2**63 - 1, id="int64_max"),
        pytest.param(zigpy.types.bitmap64, 2**64 - 1, id="bitmap64_max"),
        pytest.param(zigpy.types.enum64, 2**64 - 1, id="enum64_max"),
        pytest.param(zigpy.types.uint64_t, 1, id="small_value_in_wide_type"),
    ],
)
def test_large_integer_form_round_trip(attr_type: type, value: int) -> None:
    """Preserve every integer bit through text form validation and conversion."""
    raw_value = attr_type(value)
    form_value = attribute_value_to_form_value(raw_value, attr_type)
    assert form_value == str(value)
    validated = attribute_type_to_probatio_schema(attr_type)({"value": form_value})
    converted = form_value_to_attribute_value(validated["value"], attr_type)
    assert isinstance(converted, attr_type)
    assert converted.serialize() == raw_value.serialize()


def test_nested_large_integer_form_round_trip() -> None:
    """Preserve exact integer text inside struct and list form controls."""
    raw_value = _LargeIntegerStruct(signed=-(2**63) + 1, unsigned=[2**64 - 1])
    form_value = attribute_value_to_form_value(raw_value, _LargeIntegerStruct)
    assert form_value == {
        "signed": "-9223372036854775807",
        "unsigned": [{"value": "18446744073709551615"}],
    }
    validated = attribute_type_to_probatio_schema(_LargeIntegerStruct)(
        {"value": form_value}
    )
    converted = form_value_to_attribute_value(validated["value"], _LargeIntegerStruct)
    assert converted.serialize() == raw_value.serialize()


def test_attribute_value_to_form_value_none_returns_none() -> None:
    """Test read conversion preserves `None`."""
    assert attribute_value_to_form_value(None, _TestEnum) is None


def test_attribute_value_to_form_value_bytes_inputs() -> None:
    """Test bytes values are converted to lower-case hex."""
    assert attribute_value_to_form_value(b"\x01\x02\xff", bytes) == "0102ff"


def test_attribute_value_to_form_value_serializable_bytes_inputs() -> None:
    """Test serializable bytes values are converted to lower-case hex."""
    value = zigpy.types.SerializableBytes(b"\x01\x02\xff")
    assert (
        attribute_value_to_form_value(value, zigpy.types.SerializableBytes) == "0102ff"
    )


def test_attribute_value_to_form_value_serializable_bytes_raw_bytes_inputs() -> None:
    """Test raw bytes values still map to hex for serializable byte types."""
    assert (
        attribute_value_to_form_value(
            bytearray(b"\x01\x02\xff"), zigpy.types.SerializableBytes
        )
        == "0102ff"
    )


def test_attribute_value_to_form_value_float_inputs() -> None:
    """Test float values are converted to native float for ha-form."""
    assert attribute_value_to_form_value(
        zigpy.types.Single(1.25), zigpy.types.Single
    ) == pytest.approx(1.25)


def test_attribute_value_to_form_value_struct_inputs() -> None:
    """Test struct values are converted to dictionaries for object selector editing."""
    value = _TestStruct(field_a=1, field_b=2)
    assert attribute_value_to_form_value(value, _TestStruct) == {
        "field_a": 1,
        "field_b": 2,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(_TestOptionalStruct(required=1), {"required": 1}, id="omitted"),
        pytest.param(
            _TestOptionalStruct(required=1, optional=2),
            {"required": 1, "optional": 2},
            id="provided",
        ),
    ],
)
def test_optional_struct_form_round_trip(
    value: _TestOptionalStruct, expected: dict[str, int]
) -> None:
    """Omit absent optional fields so their controls accept read form values."""
    form_value = attribute_value_to_form_value(value, _TestOptionalStruct)
    assert form_value == expected
    validated = attribute_type_to_probatio_schema(_TestOptionalStruct)(
        {"value": form_value}
    )
    converted = form_value_to_attribute_value(validated["value"], _TestOptionalStruct)
    assert converted.serialize() == _TestOptionalStruct(**expected).serialize()


def test_attribute_value_to_form_value_struct_drops_unknown_fields() -> None:
    """Test struct form conversion drops keys not declared in the struct."""
    form_value = attribute_value_to_form_value(
        {"field_a": 1, "field_b": 2, "extra": 3},
        _TestStruct,
    )
    assert form_value == {"field_a": 1, "field_b": 2}
    assert form_value_to_attribute_value(form_value, _TestStruct) == _TestStruct(
        field_a=1,
        field_b=2,
    )


def test_attribute_value_to_form_value_struct_invalid_shape_falls_back_to_string() -> (
    None
):
    """Test non-struct data falls back to string form rendering."""
    assert attribute_value_to_form_value(7, _TestStruct) == "7"


def test_attribute_value_to_form_value_list_inputs() -> None:
    """Test list-like values are converted to strict object-list values."""
    assert attribute_value_to_form_value(_TestList([1, 2, 3]), _TestList) == [
        {"value": 1},
        {"value": 2},
        {"value": 3},
    ]


def test_attribute_value_to_form_value_list_invalid_shape_falls_back_to_string() -> (
    None
):
    """Test non-list data falls back to string form rendering."""
    assert attribute_value_to_form_value("not-a-list", _TestList) == "not-a-list"


def test_attribute_value_to_form_value_list_without_item_type_keeps_raw_items() -> None:
    """Test list conversion keeps raw values when no item type is available."""
    assert attribute_value_to_form_value(_UntypedList([1, 2]), _UntypedList) == [
        {"value": 1},
        {"value": 2},
    ]


def test_attribute_value_to_form_value_eui64_inputs() -> None:
    """Test EUI64 values are converted to human-readable colon-delimited text."""
    value = zigpy.types.EUI64.convert("01:02:03:04:05:06:07:08")
    assert attribute_value_to_form_value(value, zigpy.types.EUI64) == str(value)


def test_attribute_value_to_form_value_keydata_inputs() -> None:
    """Test KeyData values are converted to human-readable colon-delimited text."""
    value = zigpy.types.KeyData.convert("000102030405060708090a0b0c0d0e0f")
    assert attribute_value_to_form_value(value, zigpy.types.KeyData) == str(value)


def test_attribute_value_to_form_value_unknown_type_fallback_string() -> None:
    """Test unsupported attribute types render values as strings."""
    assert attribute_value_to_form_value(123, _UnsupportedSelectorType) == "123"


@pytest.mark.parametrize(
    ("obj", "expected_output"),
    [
        ({"a": 1, "b": 2, "c": None}, {"a": 1, "b": 2}),
        ({"a": 1, "b": 2, "c": 0}, {"a": 1, "b": 2, "c": 0}),
        ({"a": 1, "b": 2, "c": ""}, {"a": 1, "b": 2, "c": ""}),
        ({"a": 1, "b": 2, "c": False}, {"a": 1, "b": 2, "c": False}),
    ],
)
def test_exclude_none_values(
    obj: dict[str, Any], expected_output: dict[str, Any]
) -> None:
    """Test exclude_none_values helper."""
    result = exclude_none_values(obj)
    assert result == expected_output

    for key, value in expected_output.items():
        assert value == obj[key]


async def test_create_zha_config_remove_unused(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_zigpy_connect: ControllerApplication,
) -> None:
    """Test creating ZHA config data with unused keys."""
    config_entry.add_to_hass(hass)

    options = config_entry.options.copy()
    options["custom_configuration"]["zha_options"]["some_random_key"] = "a value"

    hass.config_entries.async_update_entry(config_entry, options=options)

    assert (
        config_entry.options["custom_configuration"]["zha_options"]["some_random_key"]
        == "a value"
    )

    status = await async_setup_component(
        hass,
        zha_const.DOMAIN,
        {zha_const.DOMAIN: {zha_const.CONF_ENABLE_QUIRKS: False}},
    )
    assert status is True
    await hass.async_block_till_done()

    ha_zha_data = get_zha_data(hass)

    # Does not error out
    create_zha_config(hass, ha_zha_data)
