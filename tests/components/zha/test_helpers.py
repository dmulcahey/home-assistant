"""Tests for ZHA helpers."""

import enum
import logging
from typing import Any, cast
from unittest.mock import patch

from probatio import to_field_list
import pytest
from zigpy.application import ControllerApplication
import zigpy.types
from zigpy.types.basic import uint16_t
from zigpy.zcl.clusters import general, lighting, security

from homeassistant.components.zha import const as zha_const
from homeassistant.components.zha.helpers import (
    attribute_type_to_vol_schema,
    attribute_value_to_form_value,
    cluster_command_schema_to_vol_schema,
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


class _TestFlags(enum.Flag):
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


async def test_zcl_schema_conversions(hass: HomeAssistant) -> None:
    """Test ZHA ZCL schema conversion helpers."""
    command_schema = lighting.Color.ServerCommandDefs.color_loop_set.schema
    expected_schema = [
        {
            "type": "multi_select",
            "options": {
                "Action": "Action",
                "Direction": "Direction",
                "Time": "Time",
                "Start Hue": "Start Hue",
            },
            "name": "update_flags",
            "required": True,
        },
        {
            "type": "select",
            "options": [
                ("Deactivate", "Deactivate"),
                ("Activate from color loop hue", "Activate from color loop hue"),
                ("Activate from current hue", "Activate from current hue"),
            ],
            "name": "action",
            "required": True,
        },
        {
            "type": "select",
            "options": [("Decrement", "Decrement"), ("Increment", "Increment")],
            "name": "direction",
            "required": True,
        },
        {
            "selector": {
                "number": {
                    "min": 0.0,
                    "max": 65535.0,
                    "step": 1.0,
                    "mode": "box",
                }
            },
            "name": "time",
            "required": True,
        },
        {
            "selector": {
                "number": {
                    "min": 0.0,
                    "max": 65535.0,
                    "step": 1.0,
                    "mode": "box",
                }
            },
            "name": "start_hue",
            "required": True,
        },
        {
            "type": "multi_select",
            "options": {"Execute if off present": "Execute if off present"},
            "name": "options_mask",
            "optional": True,
            "required": False,
        },
        {
            "type": "multi_select",
            "options": {"Execute if off": "Execute if off"},
            "name": "options_override",
            "optional": True,
            "required": False,
        },
    ]
    vol_schema = to_field_list(
        cluster_command_schema_to_vol_schema(command_schema),
        custom_serializer=cv.custom_serializer,
    )
    assert vol_schema == expected_schema

    raw_data = {
        "update_flags": ["Action", "Start Hue"],
        "action": "Activate from current hue",
        "direction": "Increment",
        "time": 20,
        "start_hue": 196,
    }

    converted_data = convert_to_zcl_values(raw_data, command_schema)

    assert isinstance(
        converted_data["update_flags"], lighting.Color.ColorLoopUpdateFlags
    )
    assert lighting.Color.ColorLoopUpdateFlags.Action in converted_data["update_flags"]
    assert (
        lighting.Color.ColorLoopUpdateFlags.Start_Hue in converted_data["update_flags"]
    )

    assert isinstance(converted_data["action"], lighting.Color.ColorLoopAction)
    assert (
        converted_data["action"]
        == lighting.Color.ColorLoopAction.Activate_from_current_hue
    )

    assert isinstance(converted_data["direction"], lighting.Color.ColorLoopDirection)
    assert converted_data["direction"] == lighting.Color.ColorLoopDirection.Increment

    assert isinstance(converted_data["time"], uint16_t)
    assert converted_data["time"] == 20

    assert isinstance(converted_data["start_hue"], uint16_t)
    assert converted_data["start_hue"] == 196

    # This time, the update flags bitmap is empty.
    raw_data = {
        "update_flags": [],
        "action": "Activate from current hue",
        "direction": "Increment",
        "time": 20,
        "start_hue": 196,
    }

    converted_data = convert_to_zcl_values(raw_data, command_schema)

    # No flags are passed through
    assert converted_data["update_flags"] == 0


@pytest.mark.parametrize(
    ("attr_type", "expected_schema"),
    [
        (
            zigpy.types.Bool,
            [{"type": "boolean", "name": "value", "required": True}],
        ),
        (
            _TestFlags,
            [
                {
                    "type": "multi_select",
                    "options": {"Option A": "Option A", "Option B": "Option B"},
                    "name": "value",
                    "required": True,
                }
            ],
        ),
        (
            _TestEnum,
            [
                {
                    "type": "select",
                    "options": [("Option A", "Option A"), ("Option B", "Option B")],
                    "name": "value",
                    "required": True,
                }
            ],
        ),
        (
            zigpy.types.uint8_t,
            [
                {
                    "selector": {
                        "number": {
                            "min": 0.0,
                            "max": 255.0,
                            "step": 1.0,
                            "mode": "box",
                        }
                    },
                    "name": "value",
                    "required": True,
                }
            ],
        ),
        (
            zigpy.types.Single,
            [
                {
                    "selector": {"number": {"step": "any", "mode": "box"}},
                    "name": "value",
                    "required": True,
                }
            ],
        ),
        (
            bytes,
            [{"type": "string", "name": "value", "required": True}],
        ),
        (
            zigpy.types.EUI64,
            [{"type": "string", "name": "value", "required": True}],
        ),
        (
            zigpy.types.KeyData,
            [{"type": "string", "name": "value", "required": True}],
        ),
        (
            _TestStruct,
            [
                {
                    "selector": {
                        "object": {
                            "multiple": False,
                            "label_field": "field_a",
                            "fields": {
                                "field_a": {
                                    "required": True,
                                    "selector": {
                                        "number": {
                                            "min": 0.0,
                                            "max": 255.0,
                                            "step": 1,
                                            "mode": "box",
                                        }
                                    },
                                },
                                "field_b": {
                                    "required": True,
                                    "selector": {
                                        "number": {
                                            "min": 0.0,
                                            "max": 65535.0,
                                            "step": 1,
                                            "mode": "box",
                                        }
                                    },
                                },
                            },
                        }
                    },
                    "name": "value",
                    "required": True,
                }
            ],
        ),
        (
            _TestList,
            [
                {
                    "selector": {
                        "object": {
                            "multiple": True,
                            "label_field": "value",
                            "fields": {
                                "value": {
                                    "required": True,
                                    "selector": {
                                        "number": {
                                            "min": 0.0,
                                            "max": 255.0,
                                            "step": 1,
                                            "mode": "box",
                                        }
                                    },
                                }
                            },
                        }
                    },
                    "name": "value",
                    "required": True,
                }
            ],
        ),
    ],
)
def test_attribute_type_to_vol_schema_shapes(
    attr_type: type[Any], expected_schema: list[dict[str, Any]]
) -> None:
    """Test typed attribute schemas are serialized with exact expected form shapes."""
    assert (
        to_field_list(
            attribute_type_to_vol_schema(attr_type),
            custom_serializer=cv.custom_serializer,
        )
        == expected_schema
    )


def test_attribute_type_to_vol_schema_non_type_falls_back_to_text() -> None:
    """Test non-type schema inputs safely fall back to text values."""
    assert to_field_list(
        attribute_type_to_vol_schema(cast(Any, 123)),
        custom_serializer=cv.custom_serializer,
    ) == [{"type": "string", "name": "value", "required": True}]


def test_attribute_type_to_vol_schema_selector_branch_coverage() -> None:
    """Test nested selector generation covers selector mapping branches."""
    schema = cast(
        list[dict[str, Any]],
        to_field_list(
            attribute_type_to_vol_schema(_SelectorCoverageStruct),
            custom_serializer=cv.custom_serializer,
        ),
    )
    assert set(schema[0]["selector"]["object"]["fields"]) == {
        "bool_field",
        "flag_field",
        "enum_field",
        "struct_field",
        "int_field",
        "float_field",
        "text_field",
        "list_field",
        "fallback_field",
    }


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


def test_form_value_to_attribute_value_flag_invalid_member() -> None:
    """Test invalid flag members are rejected with an explicit error."""
    with pytest.raises(ValueError, match="Invalid flag member"):
        form_value_to_attribute_value(["Not A Real Flag"], _TestFlags)


def test_form_value_to_attribute_value_flag_invalid_payload_type() -> None:
    """Test invalid flag list payload items fail loudly."""
    with pytest.raises(ValueError, match="Flag attributes require"):
        form_value_to_attribute_value(3, _TestFlags)


def test_form_value_to_attribute_value_flag_invalid_item_type() -> None:
    """Test invalid flag list item types are rejected."""
    with pytest.raises(ValueError, match="Flag attributes require list items"):
        form_value_to_attribute_value(["Option A", 2], _TestFlags)


@pytest.mark.parametrize(
    ("raw_value", "expected_value"),
    [(False, False), (True, True)],
)
def test_form_value_to_attribute_value_bool_inputs(
    raw_value: Any, expected_value: bool
) -> None:
    """Test bool conversion consumes strict boolean values."""
    assert form_value_to_attribute_value(raw_value, zigpy.types.Bool) is expected_value


def test_form_value_to_attribute_value_bool_invalid_member() -> None:
    """Test invalid boolean values are rejected with an explicit error."""
    with pytest.raises(ValueError, match="Boolean attributes only accept"):
        form_value_to_attribute_value("true", zigpy.types.Bool)


def test_form_value_to_attribute_value_enum_inputs() -> None:
    """Test enum conversion supports form labels."""
    assert form_value_to_attribute_value("Option A", _TestEnum) is _TestEnum.Option_A
    assert form_value_to_attribute_value("Option B", _TestEnum) is _TestEnum.Option_B


def test_form_value_to_attribute_value_enum_invalid_member() -> None:
    """Test invalid enum inputs are rejected with an explicit error."""
    with pytest.raises(ValueError, match="Invalid enum"):
        form_value_to_attribute_value("Not an option", _TestEnum)


def test_form_value_to_attribute_value_enum_invalid_numeric_member() -> None:
    """Test invalid numeric enum inputs are rejected with an explicit error."""
    with pytest.raises(ValueError, match="Invalid enum value"):
        form_value_to_attribute_value(999, _TestEnum)


def test_form_value_to_attribute_value_bytes_inputs() -> None:
    """Test bytes conversion consumes hex strings."""
    assert form_value_to_attribute_value("0102ff", bytes) == b"\x01\x02\xff"


def test_form_value_to_attribute_value_serializable_bytes_inputs() -> None:
    """Test serializable bytes conversion consumes hex strings."""
    converted = form_value_to_attribute_value("0102ff", zigpy.types.SerializableBytes)
    assert isinstance(converted, zigpy.types.SerializableBytes)
    assert converted.value == b"\x01\x02\xff"


def test_form_value_to_attribute_value_bytes_invalid_hex() -> None:
    """Test invalid hex inputs are rejected for bytes values."""
    with pytest.raises(ValueError, match="Invalid hex value"):
        form_value_to_attribute_value("zz-not-hex", bytes)


def test_form_value_to_attribute_value_bytes_invalid_payload_type() -> None:
    """Test non-string bytes payloads are rejected."""
    with pytest.raises(ValueError, match="Invalid hex value"):
        form_value_to_attribute_value(123, bytes)


@pytest.mark.parametrize("raw_value", ["b'\\x01\\x02\\xff'", "'0102'", "b'abc"])
@pytest.mark.parametrize("attr_type", [bytes, zigpy.types.SerializableBytes])
def test_form_value_to_attribute_value_bytes_literal_rejected(
    raw_value: str, attr_type: type[bytes | zigpy.types.SerializableBytes]
) -> None:
    """Test bytes-literal text is rejected for byte-like values."""
    with pytest.raises(ValueError, match="Invalid hex value"):
        form_value_to_attribute_value(raw_value, attr_type)


def test_form_value_to_attribute_value_float_inputs() -> None:
    """Test float conversion consumes numeric values."""
    value_from_float = form_value_to_attribute_value(1.25, zigpy.types.Single)
    assert isinstance(value_from_float, zigpy.types.Single)
    assert float(value_from_float) == pytest.approx(1.25)

    value_from_int = form_value_to_attribute_value(2, zigpy.types.Single)
    assert isinstance(value_from_int, zigpy.types.Single)
    assert float(value_from_int) == pytest.approx(2.0)


def test_form_value_to_attribute_value_float_invalid_inputs() -> None:
    """Test invalid float values are rejected."""
    with pytest.raises(ValueError, match="Invalid float value"):
        form_value_to_attribute_value("2.75", zigpy.types.Single)


def test_form_value_to_attribute_value_float_constructor_error() -> None:
    """Test float conversion normalizes constructor errors."""
    with (
        patch.object(zigpy.types.Single, "__new__", side_effect=ValueError("boom")),
        pytest.raises(ValueError, match="Invalid float value"),
    ):
        form_value_to_attribute_value(1.25, zigpy.types.Single)


def test_form_value_to_attribute_value_integer_invalid_inputs() -> None:
    """Test invalid integer values are rejected."""
    with pytest.raises(ValueError, match="Invalid integer value"):
        form_value_to_attribute_value("not-an-int", zigpy.types.uint8_t)


def test_form_value_to_attribute_value_integer_integral_float_input() -> None:
    """Test integer conversion accepts integral float payloads."""
    converted = form_value_to_attribute_value(1.0, zigpy.types.uint8_t)
    assert isinstance(converted, zigpy.types.uint8_t)
    assert converted == 1


def test_form_value_to_attribute_value_integer_fractional_float_rejected() -> None:
    """Test integer conversion rejects fractional float payloads."""
    with pytest.raises(ValueError, match="Invalid integer value"):
        form_value_to_attribute_value(1.2, zigpy.types.uint8_t)


def test_form_value_to_attribute_value_integer_bool_input_rejected() -> None:
    """Test integer conversion rejects boolean payloads."""
    with pytest.raises(ValueError, match="Invalid integer value"):
        form_value_to_attribute_value(True, zigpy.types.uint8_t)


def test_form_value_to_attribute_value_integer_out_of_range_rejected() -> None:
    """Test integer conversion rejects out-of-range values."""
    with pytest.raises(ValueError, match="Invalid integer value"):
        form_value_to_attribute_value(256, zigpy.types.uint8_t)


def test_form_value_to_attribute_value_struct_inputs() -> None:
    """Test struct conversion consumes object-form values."""
    value = form_value_to_attribute_value({"field_a": 1, "field_b": 2}, _TestStruct)
    assert isinstance(value, _TestStruct)
    assert value.field_a == 1
    assert value.field_b == 2


def test_form_value_to_attribute_value_struct_invalid_nested_inputs() -> None:
    """Test invalid struct fields fail loudly with explicit errors."""
    with pytest.raises(ValueError, match="Invalid integer value"):
        form_value_to_attribute_value({"field_a": "invalid", "field_b": 2}, _TestStruct)


def test_form_value_to_attribute_value_struct_unexpected_field() -> None:
    """Test unknown struct fields are rejected."""
    with pytest.raises(ValueError, match="Unexpected struct field"):
        form_value_to_attribute_value(
            {"field_a": 1, "field_b": 2, "extra": 3}, _TestStruct
        )


def test_form_value_to_attribute_value_struct_missing_required_field() -> None:
    """Test missing required struct fields are rejected."""
    with pytest.raises(ValueError, match="Missing required struct field"):
        form_value_to_attribute_value({"field_a": 1}, _TestStruct)


def test_form_value_to_attribute_value_struct_invalid_payload_type() -> None:
    """Test non-dict struct payloads are rejected."""
    with pytest.raises(ValueError, match="Struct attributes require a dictionary"):
        form_value_to_attribute_value("invalid", _TestStruct)


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


def test_form_value_to_attribute_value_list_object_missing_value() -> None:
    """Test list-like conversion rejects malformed object-list payloads."""
    with pytest.raises(ValueError, match="include a 'value' field"):
        form_value_to_attribute_value([{"not_value": 1}], _TestList)


def test_form_value_to_attribute_value_list_invalid_payload_type() -> None:
    """Test list-like conversion rejects non-list payloads."""
    with pytest.raises(ValueError, match="List-like attributes require a list/tuple"):
        form_value_to_attribute_value("bad", _TestList)


def test_form_value_to_attribute_value_list_passthrough_instance() -> None:
    """Test list-like values already typed are accepted as-is."""
    value = _TestList([1, 2, 3])
    assert form_value_to_attribute_value(value, _TestList) is value


def test_form_value_to_attribute_value_fixed_length_list_invalid_size() -> None:
    """Test fixed-length list-like conversion rejects invalid list sizes."""
    with pytest.raises(ValueError, match="requires exactly 2 item"):
        form_value_to_attribute_value(
            [{"value": 1}, {"value": 2}, {"value": 3}], _TestFixedLengthList
        )


def test_form_value_to_attribute_value_eui64_inputs() -> None:
    """Test EUI64 conversion accepts colon-delimited text."""
    value = form_value_to_attribute_value("01:02:03:04:05:06:07:08", zigpy.types.EUI64)
    assert isinstance(value, zigpy.types.EUI64)
    assert str(value) == "01:02:03:04:05:06:07:08"


def test_form_value_to_attribute_value_eui64_passthrough_instance() -> None:
    """Test EUI64 values already typed are accepted as-is."""
    value = zigpy.types.EUI64.convert("01:02:03:04:05:06:07:08")
    assert form_value_to_attribute_value(value, zigpy.types.EUI64) is value


def test_form_value_to_attribute_value_eui64_invalid_payload_type() -> None:
    """Test non-string EUI64 payloads are rejected."""
    with pytest.raises(ValueError, match="Invalid EUI64 value"):
        form_value_to_attribute_value(123, zigpy.types.EUI64)


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


def test_form_value_to_attribute_value_keydata_invalid_payload_type() -> None:
    """Test non-string KeyData payloads are rejected."""
    with pytest.raises(ValueError, match="Invalid KeyData value"):
        form_value_to_attribute_value(123, zigpy.types.KeyData)


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
    validated = attribute_type_to_vol_schema(attr_type)({"value": form_value})
    converted = form_value_to_attribute_value(validated["value"], attr_type)
    assert isinstance(converted, attr_type)
    assert converted == value


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
