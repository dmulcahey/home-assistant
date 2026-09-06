"""Test ZHA WebSocket API."""

from binascii import unhexlify
from collections.abc import Callable, Coroutine
from copy import deepcopy
import enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
import voluptuous as vol
from zha.application.const import (
    ATTR_ARGS,
    ATTR_ATTRIBUTE,
    ATTR_CLUSTER_ID,
    ATTR_CLUSTER_TYPE,
    ATTR_COMMAND,
    ATTR_COMMAND_TYPE,
    ATTR_ENDPOINT_ID,
    ATTR_ENDPOINT_NAMES,
    ATTR_IEEE,
    ATTR_MANUFACTURER,
    ATTR_NEIGHBORS,
    ATTR_PARAMS,
    ATTR_QUIRK_APPLIED,
    ATTR_TYPE,
    ATTR_VALUE,
    CLUSTER_TYPE_IN,
)
from zha.exceptions import ZHAException
from zha.zigbee.device import (
    ClusterBindEvent,
    ClusterConfigureReportingEvent,
    Device,
    DeviceConfiguredEvent,
)
import zigpy.backups
from zigpy.const import SIG_EP_INPUT, SIG_EP_OUTPUT, SIG_EP_PROFILE, SIG_EP_TYPE
import zigpy.exceptions
import zigpy.profiles.zha
import zigpy.types
from zigpy.types.named import EUI64
from zigpy.typing import UNDEFINED as ZIGPY_UNDEFINED
import zigpy.util
from zigpy.zcl import foundation
from zigpy.zcl.clusters import closures, general, security
from zigpy.zcl.clusters.general import Groups
import zigpy.zdo.types as zdo_types

from homeassistant.components.websocket_api import (
    ERR_INVALID_FORMAT,
    ERR_NOT_FOUND,
    ERR_UNKNOWN_ERROR,
    TYPE_RESULT,
)
from homeassistant.components.zha import DOMAIN
from homeassistant.components.zha.const import EZSP_OVERWRITE_EUI64
from homeassistant.components.zha.helpers import (
    ZHADeviceProxy,
    ZHAGatewayProxy,
    get_zha_gateway,
    get_zha_gateway_proxy,
)
from homeassistant.components.zha.websocket_api import (
    ATTR_DURATION,
    ATTR_INSTALL_CODE,
    ATTR_QR_CODE,
    ATTR_SOURCE_IEEE,
    ATTR_TARGET_IEEE,
    BINDINGS,
    CLUSTER_COMMAND_SERVER,
    GROUP_ID,
    GROUP_IDS,
    GROUP_NAME,
    ID,
    SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
    SERVICE_PERMIT,
    SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
    TYPE,
    _attribute_fixed_length,
    async_load_api,
)
from homeassistant.const import ATTR_AREA_ID, ATTR_MODEL, ATTR_NAME, Platform
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util.json import JsonValueType

from .conftest import FIXTURE_GRP_ID, FIXTURE_GRP_NAME
from .data import BASE_CUSTOM_CONFIGURATION, CONFIG_WITH_ALARM_OPTIONS

from tests.common import MockConfigEntry, MockUser
from tests.typing import MockHAClientWebSocket, WebSocketGenerator

IEEE_SWITCH_DEVICE = "01:2d:6f:00:0a:90:69:e7"
IEEE_GROUPABLE_DEVICE = "01:2d:6f:00:0a:90:69:e8"


class _ServiceTestFlags(enum.Flag):
    """Flag enum used to validate service value conversion."""

    Option_A = 1
    Option_B = 2


class _ServiceTestEnum(enum.Enum):
    """Enum used to validate service value conversion."""

    Option_A = 1
    Option_B = 2


class _ServiceTestStruct(zigpy.types.Struct):
    """Struct used to validate service and websocket value conversion."""

    field_a: zigpy.types.uint8_t
    field_b: zigpy.types.uint16_t


class _ServiceTestList(list):
    """List-like type used to validate service and websocket conversion."""

    _item_type = zigpy.types.uint8_t


class _ServiceTestFixedLengthList(list):
    """Fixed-length list-like type used to validate strict list constraints."""

    _item_type = zigpy.types.uint8_t
    _length = 2


if TYPE_CHECKING:
    from zigpy.application import ControllerApplication


@pytest.fixture(autouse=True)
def required_platform_only():
    """Only set up the required and required base platforms to speed up tests."""
    with patch(
        "homeassistant.components.zha.PLATFORMS",
        (
            Platform.ALARM_CONTROL_PANEL,
            Platform.SELECT,
            Platform.SENSOR,
            Platform.SWITCH,
        ),
    ):
        yield


@pytest.fixture
def speed_up_radio_mgr():
    """Speed up the radio manager connection time by removing delays.

    This fixture replaces the fixture in conftest.py by patching the connect
    and shutdown delays to 0 to allow waiting for the patched delays when
    running tests with time frozen, which otherwise blocks forever.
    """
    with (
        patch("homeassistant.components.zha.radio_manager.CONNECT_DELAY_S", 0),
        patch("zha.application.gateway.SHUT_DOWN_DELAY_S", 0),
    ):
        yield


@pytest.fixture
async def zha_client(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., Device],
) -> MockHAClientWebSocket:
    """Get ZHA WebSocket client."""

    await setup_zha()
    gateway = get_zha_gateway(hass)

    zigpy_device_switch = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [general.OnOff.cluster_id, general.Basic.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
        ieee=IEEE_SWITCH_DEVICE,
    )

    zigpy_device_groupable = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [
                    general.OnOff.cluster_id,
                    general.Basic.cluster_id,
                    general.Groups.cluster_id,
                ],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
        ieee=IEEE_GROUPABLE_DEVICE,
    )

    gateway.get_or_create_device(zigpy_device_switch)
    await gateway.async_device_initialized(zigpy_device_switch)
    await hass.async_block_till_done(wait_background_tasks=True)

    gateway.get_or_create_device(zigpy_device_groupable)
    await gateway.async_device_initialized(zigpy_device_groupable)
    await hass.async_block_till_done(wait_background_tasks=True)

    # load the ZHA API
    async_load_api(hass)
    return await hass_ws_client(hass)


async def test_device_clusters(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test getting device cluster info."""
    await zha_client.send_json(
        {ID: 5, TYPE: "zha/devices/clusters", ATTR_IEEE: IEEE_SWITCH_DEVICE}
    )

    msg = await zha_client.receive_json()

    assert len(msg["result"]) == 2

    cluster_infos = sorted(msg["result"], key=lambda k: k[ID])

    cluster_info = cluster_infos[0]
    assert cluster_info[TYPE] == CLUSTER_TYPE_IN
    assert cluster_info[ID] == 0
    assert cluster_info[ATTR_NAME] == "Basic"

    cluster_info = cluster_infos[1]
    assert cluster_info[TYPE] == CLUSTER_TYPE_IN
    assert cluster_info[ID] == 6
    assert cluster_info[ATTR_NAME] == "OnOff"


async def test_device_cluster_attributes(zha_client: MockHAClientWebSocket) -> None:
    """Test getting device cluster attributes."""
    await zha_client.send_json(
        {
            ID: 5,
            TYPE: "zha/devices/clusters/attributes",
            ATTR_ENDPOINT_ID: 1,
            ATTR_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_CLUSTER_ID: 6,
            ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
        }
    )

    msg = await zha_client.receive_json()

    attributes = msg["result"]
    assert attributes

    for attribute in attributes:
        assert "schema" in attribute
        assert "zcl_attribute" in attribute
        assert ID not in attribute
        assert ATTR_NAME not in attribute
        assert "manufacturer_code" not in attribute
        assert isinstance(attribute["schema"], list)
        assert attribute["zcl_attribute"][ID] is not None
        assert attribute["zcl_attribute"][ATTR_NAME] is not None

    expected_ids = {int(attr.id) for attr in general.OnOff.AttributeDefs}
    result_ids = {entry["zcl_attribute"][ID] for entry in attributes}
    assert expected_ids.issubset(result_ids)

    on_off_attribute = next(
        attr
        for attr in attributes
        if attr["zcl_attribute"][ID] == general.OnOff.AttributeDefs.on_off.id
    )
    assert on_off_attribute["schema"] == [
        {"type": "boolean", "name": "value", "required": True}
    ]

    off_wait_time_attribute = next(
        attr
        for attr in attributes
        if attr["zcl_attribute"][ID] == general.OnOff.AttributeDefs.off_wait_time.id
    )
    assert off_wait_time_attribute["schema"] == [
        {
            "selector": {
                "number": {
                    "min": 0.0,
                    "max": 65535.0,
                    "step": 1.0,
                    "mode": "box",
                }
            },
            "name": "value",
            "required": True,
        }
    ]


async def test_device_cluster_attributes_invalid_cluster_returns_error(
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test attributes websocket returns an error for invalid cluster lookups."""
    await zha_client.send_json(
        {
            ID: 56,
            TYPE: "zha/devices/clusters/attributes",
            ATTR_ENDPOINT_ID: 1,
            ATTR_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_CLUSTER_ID: 0xFFFF,
            ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
        }
    )

    msg = await zha_client.receive_json()

    assert msg["id"] == 56
    assert msg["success"] is False
    assert msg["error"]["code"] == ERR_UNKNOWN_ERROR


async def test_device_cluster_attributes_complex_schema_shapes(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket schema serialization for complex attribute types."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )

    attrs = [
        foundation.ZCLAttributeDef(
            id=101,
            name="custom_struct",
            type=_ServiceTestStruct,
        ),
        foundation.ZCLAttributeDef(
            id=102,
            name="custom_list",
            type=_ServiceTestList,
        ),
        foundation.ZCLAttributeDef(
            id=103, name="custom_fixed_list", type=_ServiceTestFixedLengthList
        ),
        foundation.ZCLAttributeDef(
            id=104,
            name="custom_float",
            type=zigpy.types.Single,
            zcl_type=foundation.DataTypeId.single,
        ),
    ]

    with patch.object(cluster, "AttributeDefs", attrs):
        await zha_client.send_json(
            {
                ID: 55,
                TYPE: "zha/devices/clusters/attributes",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            }
        )
        msg = await zha_client.receive_json()

    by_id = {entry["zcl_attribute"][ID]: entry for entry in msg["result"]}
    assert by_id[101]["schema"] == [
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
    ]
    assert by_id[102]["schema"] == [
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
    ]
    assert by_id[103]["fixed_length"] == 2
    assert by_id[104]["schema"] == [
        {
            "selector": {"number": {"step": "any", "mode": "box"}},
            "name": "value",
            "required": True,
        }
    ]


def test_attribute_fixed_length_helper_special_cases() -> None:
    """Test fixed-length helper edge cases for non-list and special list types."""
    assert _attribute_fixed_length(zigpy.types.EUI64) is None
    assert _attribute_fixed_length(zigpy.types.KeyData) is None
    assert _attribute_fixed_length(cast(Any, 1)) is None


async def test_device_cluster_attributes_no_type_has_empty_schema(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket attribute schema is empty when attribute type is unknown."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attr_without_type = SimpleNamespace(
        id=105,
        name="custom_no_type",
        type=None,
        zcl_type=None,
        access=None,
        mandatory=False,
        is_manufacturer_specific=False,
        manufacturer_code=None,
    )

    with patch.object(cluster, "AttributeDefs", [attr_without_type]):
        await zha_client.send_json(
            {
                ID: 58,
                TYPE: "zha/devices/clusters/attributes",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            }
        )
        msg = await zha_client.receive_json()

    result_entry = msg["result"][0]
    assert result_entry["schema"] == []
    assert result_entry["zcl_attribute"][ATTR_TYPE] is None


async def test_device_cluster_attributes_include_manufacturer_specific_defs(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket includes both standard and manufacturer-specific definitions."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )

    shared_id = 0x1234
    standard_attr = foundation.ZCLAttributeDef(
        id=shared_id,
        name="shared_attr",
        type=zigpy.types.Bool,
        zcl_type=foundation.DataTypeId.bool_,
        access=foundation.ZCLAttributeAccess.Read,
        mandatory=False,
        is_manufacturer_specific=False,
        manufacturer_code=None,
    )
    manufacturer_attr = foundation.ZCLAttributeDef(
        id=shared_id,
        name="shared_attr_manufacturer",
        type=zigpy.types.Bool,
        zcl_type=foundation.DataTypeId.bool_,
        access=foundation.ZCLAttributeAccess.Read,
        mandatory=False,
        is_manufacturer_specific=True,
        manufacturer_code=0x1234,
    )

    with patch.object(
        cluster,
        "AttributeDefs",
        [standard_attr, manufacturer_attr],
    ):
        await zha_client.send_json(
            {
                ID: 56,
                TYPE: "zha/devices/clusters/attributes",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            }
        )
        msg = await zha_client.receive_json()

    attributes = msg["result"]
    assert len(attributes) == 2

    standard_entry = next(
        entry
        for entry in attributes
        if entry["zcl_attribute"][ATTR_NAME] == "shared_attr"
    )
    assert standard_entry["zcl_attribute"][ID] == shared_id
    assert standard_entry["zcl_attribute"][ATTR_TYPE] == "Bool"
    assert standard_entry["zcl_attribute"]["zcl_type"] == int(
        foundation.DataTypeId.bool_
    )
    assert (
        standard_entry["zcl_attribute"]["access"]
        == foundation.ZCLAttributeAccess.Read.value
    )
    assert standard_entry["zcl_attribute"]["mandatory"] is False
    assert standard_entry["zcl_attribute"]["is_manufacturer_specific"] is False
    assert standard_entry["zcl_attribute"]["manufacturer_code"] is None
    assert ID not in standard_entry
    assert ATTR_NAME not in standard_entry
    assert "manufacturer_code" not in standard_entry

    manufacturer_entry = next(
        entry
        for entry in attributes
        if entry["zcl_attribute"][ATTR_NAME] == "shared_attr_manufacturer"
    )
    assert manufacturer_entry["zcl_attribute"][ID] == shared_id
    assert manufacturer_entry["zcl_attribute"][ATTR_TYPE] == "Bool"
    assert manufacturer_entry["zcl_attribute"]["zcl_type"] == int(
        foundation.DataTypeId.bool_
    )
    assert (
        manufacturer_entry["zcl_attribute"]["access"]
        == foundation.ZCLAttributeAccess.Read.value
    )
    assert manufacturer_entry["zcl_attribute"]["mandatory"] is False
    assert manufacturer_entry["zcl_attribute"]["is_manufacturer_specific"] is True
    assert manufacturer_entry["zcl_attribute"]["manufacturer_code"] == 0x1234
    assert ID not in manufacturer_entry
    assert ATTR_NAME not in manufacturer_entry
    assert "manufacturer_code" not in manufacturer_entry


@pytest.mark.parametrize(
    ("attr_type", "raw_value", "error"),
    [
        pytest.param(
            _ServiceTestFlags,
            3,
            "Flag attributes require",
            id="rejects_legacy_flag_int_input",
        ),
        pytest.param(
            _ServiceTestFlags,
            "03",
            "Flag attributes require",
            id="rejects_legacy_flag_zero_padded_text_input",
        ),
        pytest.param(
            zigpy.types.Bool,
            "false",
            "Boolean attributes only accept",
            id="rejects_legacy_bool_text_input",
        ),
        pytest.param(
            _ServiceTestFlags,
            ["Not a valid flag"],
            "Invalid flag member",
            id="invalid_flag_fails_loudly",
        ),
        pytest.param(
            zigpy.types.Bool,
            "invalid-bool",
            "Boolean attributes only accept",
            id="invalid_bool_fails_loudly",
        ),
        pytest.param(
            zigpy.types.uint8_t,
            1.2,
            "Invalid integer value",
            id="integer_fractional_float_fails_loudly",
        ),
        pytest.param(
            zigpy.types.Single,
            "not-a-float",
            "Invalid float value",
            id="invalid_float_fails_loudly",
        ),
        pytest.param(
            bytes,
            "zz-not-hex",
            "Invalid hex value",
            id="invalid_bytes_hex_fails_loudly",
        ),
        pytest.param(
            _ServiceTestStruct,
            {"field_a": "invalid", "field_b": 2},
            "Invalid integer value",
            id="invalid_struct_fails_loudly",
        ),
        pytest.param(
            _ServiceTestFixedLengthList,
            [{"value": 1}, {"value": 2}, {"value": 3}],
            "requires exactly 2 item",
            id="fixed_length_list_invalid_size_fails_loudly",
        ),
    ],
)
async def test_set_cluster_attribute_invalid_value(
    hass: HomeAssistant,
    zha_client: MockHAClientWebSocket,
    hass_admin_user: MockUser,
    attr_type: type,
    raw_value: JsonValueType,
    error: str,
) -> None:
    """Test invalid form values are rejected before writing to the device."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    write_attribute_mock = AsyncMock(return_value=None)

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=attr_type),
        ),
        patch.object(zha_device, "write_zigbee_attribute", write_attribute_mock),
        pytest.raises(ValueError, match=error),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: raw_value,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    write_attribute_mock.assert_not_awaited()


async def test_set_cluster_attribute_prefers_manufacturer_specific_attribute_type(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer-specific attribute defs are used before generic lookup."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    cluster_write_mock = AsyncMock(return_value=[[]])
    device_write_mock = AsyncMock(return_value=None)
    manufacturer_code = 0x1234

    with (
        patch.dict(cluster.attributes, {attribute: MagicMock(type=zigpy.types.Bool)}),
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestEnum),
        ) as find_attribute_mock,
        patch.object(cluster, "write_attributes", cluster_write_mock),
        patch.object(zha_device, "write_zigbee_attribute", device_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: "Option B",
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    find_attribute_mock.assert_called_once_with(
        attribute, manufacturer_code=manufacturer_code
    )
    assert device_write_mock.await_count == 0
    assert cluster_write_mock.await_count == 1
    assert cluster_write_mock.await_args.args[0] == {
        attribute: _ServiceTestEnum.Option_B
    }
    assert cluster_write_mock.await_args.kwargs["manufacturer"] == manufacturer_code


async def test_set_cluster_attribute_accepts_attribute_name_with_manufacturer(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer-specific writes support attribute names plus manufacturer."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute_name = general.OnOff.AttributeDefs.on_off.name
    cluster_write_mock = AsyncMock(return_value=[[]])
    manufacturer_code = 0x1234

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(
                id=general.OnOff.AttributeDefs.on_off.id,
                type=_ServiceTestEnum,
            ),
        ) as find_attribute_mock,
        patch.object(cluster, "write_attributes", cluster_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute_name,
                ATTR_VALUE: "Option B",
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    find_attribute_mock.assert_called_once_with(
        attribute_name, manufacturer_code=manufacturer_code
    )
    assert cluster_write_mock.await_count == 1
    assert cluster_write_mock.await_args.args[0] == {
        attribute_name: _ServiceTestEnum.Option_B
    }
    assert cluster_write_mock.await_args.kwargs["manufacturer"] == manufacturer_code


async def test_set_cluster_attribute_prefers_manufacturer_specific_real_attr_type(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer-specific conversion using a real cluster attr definition."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    manufacturer_code = 0x1234
    cluster_write_mock = AsyncMock(return_value=[[]])
    device_write_mock = AsyncMock(return_value=None)
    real_attr_def = general.Basic.AttributeDefs.model

    with (
        patch.dict(cluster.attributes, {attribute: MagicMock(type=zigpy.types.Bool)}),
        patch.object(cluster, "find_attribute", return_value=real_attr_def),
        patch.object(cluster, "write_attributes", cluster_write_mock),
        patch.object(zha_device, "write_zigbee_attribute", device_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: "Kitchen Switch",
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert device_write_mock.await_count == 0
    assert cluster_write_mock.await_count == 1
    converted = cluster_write_mock.await_args.args[0][attribute]
    assert isinstance(converted, real_attr_def.type)
    assert str(converted) == "Kitchen Switch"
    assert cluster_write_mock.await_args.kwargs["manufacturer"] == manufacturer_code


async def test_set_cluster_attribute_manufacturer_write_failure_is_normalized(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer-specific write failures raise the standard ZHA exception."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    manufacturer_code = 0x1234

    with (
        patch.dict(cluster.attributes, {attribute: MagicMock(type=zigpy.types.Bool)}),
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=zigpy.types.Bool),
        ),
        patch.object(
            cluster,
            "write_attributes",
            AsyncMock(side_effect=zigpy.exceptions.ZigbeeException("boom")),
        ),
        pytest.raises(ZHAException, match="Failed to set attribute"),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: True,
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


async def test_set_cluster_attribute_rejects_manufacturer_minus_one(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test service validation rejects the legacy manufacturer=-1 sentinel."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: general.OnOff.AttributeDefs.on_off.name,
                ATTR_VALUE: True,
                ATTR_MANUFACTURER: -1,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


async def test_set_cluster_attribute_invalid_cluster_keeps_value_error(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test invalid cluster uses the existing write_zigbee_attribute error."""
    with pytest.raises(
        ValueError,
        match=(
            "Cluster 8 not found on endpoint 1 while writing attribute 0 with value 1"
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.LevelControl.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: general.OnOff.AttributeDefs.on_off.id,
                ATTR_VALUE: 1,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


async def test_set_cluster_attribute_invalid_cluster_with_manufacturer_keeps_value_error(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer-specific write preserves normalized invalid cluster error."""
    with pytest.raises(
        ValueError,
        match=(
            "Cluster 8 not found on endpoint 1 while writing attribute 0 with value 1"
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.LevelControl.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: general.OnOff.AttributeDefs.on_off.id,
                ATTR_VALUE: 1,
                ATTR_MANUFACTURER: 0x1234,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


async def test_set_cluster_attribute_lookup_failure_uses_raw_service_value(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test unresolved attribute definitions keep the original write value."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    raw_value = "not-converted"
    write_attribute_mock = AsyncMock(return_value=None)

    with (
        patch.object(cluster, "find_attribute", side_effect=KeyError),
        patch.object(zha_device, "write_zigbee_attribute", write_attribute_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: raw_value,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert write_attribute_mock.await_count == 1
    assert write_attribute_mock.await_args.args[3] == raw_value


async def test_set_cluster_attribute_lookup_failure_manufacturer_keeps_raw_value(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test manufacturer writes keep raw value when attr lookup fails."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    raw_value = "not-converted"
    manufacturer_code = 0x1234
    write_attributes_mock = AsyncMock(return_value=[[]])

    with (
        patch.object(cluster, "find_attribute", side_effect=KeyError),
        patch.object(cluster, "write_attributes", write_attributes_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: raw_value,
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert write_attributes_mock.await_count == 1
    assert write_attributes_mock.await_args.args[0] == {attribute: raw_value}
    assert write_attributes_mock.await_args.kwargs["manufacturer"] == manufacturer_code


async def test_set_cluster_attribute_unknown_type_keeps_raw_value(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test unknown attribute types skip conversion and write raw value."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    raw_value = "not-converted"
    write_attribute_mock = AsyncMock(return_value=None)

    with (
        patch.object(
            cluster, "find_attribute", return_value=SimpleNamespace(type=None)
        ),
        patch.object(zha_device, "write_zigbee_attribute", write_attribute_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: raw_value,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert write_attribute_mock.await_count == 1
    assert write_attribute_mock.await_args.args[3] == raw_value


@pytest.mark.parametrize(
    ("attr_type", "raw_value", "expected_value"),
    [
        pytest.param(
            _ServiceTestEnum,
            "Option A",
            _ServiceTestEnum.Option_A,
            id="preserves_enum_label_input",
        ),
        pytest.param(bytes, "0102ff", b"\x01\x02\xff", id="preserves_bytes_hex_input"),
        pytest.param(
            zigpy.types.SerializableBytes,
            "0102ff",
            "0102ff",
            id="preserves_serializable_bytes_hex_input",
        ),
        pytest.param(
            zigpy.types.Single,
            1.25,
            zigpy.types.Single(1.25),
            id="preserves_float_input",
        ),
        pytest.param(
            zigpy.types.uint8_t,
            1.0,
            zigpy.types.uint8_t(1),
            id="integer_integral_float_is_accepted",
        ),
        pytest.param(
            _ServiceTestStruct,
            {"field_a": 1, "field_b": 2},
            _ServiceTestStruct(field_a=1, field_b=2),
            id="preserves_struct_object_input",
        ),
        pytest.param(
            _ServiceTestList,
            [1, 2, 3],
            _ServiceTestList([1, 2, 3]),
            id="preserves_list_object_input",
        ),
    ],
)
async def test_set_cluster_attribute_typed_value(
    hass: HomeAssistant,
    zha_client: MockHAClientWebSocket,
    hass_admin_user: MockUser,
    attr_type: type,
    raw_value: JsonValueType,
    expected_value: object,
) -> None:
    """Test form values are converted before being passed to the device writer."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    write_attribute_mock = AsyncMock(return_value=None)

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=attr_type),
        ),
        patch.object(zha_device, "write_zigbee_attribute", write_attribute_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: raw_value,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    write_attribute_mock.assert_awaited_once_with(
        1,
        general.OnOff.cluster_id,
        attribute,
        expected_value,
        cluster_type=CLUSTER_TYPE_IN,
        manufacturer=ZIGPY_UNDEFINED,
    )
    assert isinstance(write_attribute_mock.await_args.args[3], type(expected_value))


async def test_set_cluster_attribute_serializable_bytes_default_write_path(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test default write path does not double-convert serializable bytes values."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    attr_def = MagicMock(type=zigpy.types.SerializableBytes)
    cluster_write_mock = AsyncMock(return_value=[[]])

    with (
        patch.dict(cluster.attributes, {attribute: attr_def}),
        patch.object(cluster, "find_attribute", return_value=attr_def),
        patch.object(cluster, "write_attributes", cluster_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: "0102ff",
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert cluster_write_mock.await_count == 1
    written_value = cluster_write_mock.await_args.args[0][attribute]
    assert isinstance(written_value, zigpy.types.SerializableBytes)
    assert written_value.value == b"\x01\x02\xff"


async def test_set_cluster_attribute_struct_default_write_path(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test default write path handles struct object payloads end-to-end."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    attr_def = MagicMock(type=_ServiceTestStruct)
    cluster_write_mock = AsyncMock(return_value=[[]])

    with (
        patch.dict(cluster.attributes, {attribute: attr_def}),
        patch.object(cluster, "find_attribute", return_value=attr_def),
        patch.object(cluster, "write_attributes", cluster_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: {"field_a": 1, "field_b": 2},
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert cluster_write_mock.await_count == 1
    written_value = cluster_write_mock.await_args.args[0][attribute]
    assert isinstance(written_value, _ServiceTestStruct)
    assert written_value.field_a == 1
    assert written_value.field_b == 2


async def test_set_cluster_attribute_list_default_write_path(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test default write path handles strict list object payloads end-to-end."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    attr_def = MagicMock(type=_ServiceTestList)
    cluster_write_mock = AsyncMock(return_value=[[]])

    with (
        patch.dict(cluster.attributes, {attribute: attr_def}),
        patch.object(cluster, "find_attribute", return_value=attr_def),
        patch.object(cluster, "write_attributes", cluster_write_mock),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_ZIGBEE_CLUSTER_ATTRIBUTE,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_VALUE: [{"value": 1}, {"value": 2}, {"value": 3}],
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert cluster_write_mock.await_count == 1
    written_value = cluster_write_mock.await_args.args[0][attribute]
    assert isinstance(written_value, _ServiceTestList)
    assert written_value == [1, 2, 3]


async def test_issue_cluster_command_delegates_to_device_issue_cluster_command(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test command service delegates through zha_device.issue_cluster_command."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    device_command_mock = AsyncMock(return_value=None)
    manufacturer_code = 0x1234

    with patch.object(zha_device, "issue_cluster_command", device_command_mock):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_COMMAND: 0x99,
                ATTR_COMMAND_TYPE: CLUSTER_COMMAND_SERVER,
                ATTR_PARAMS: {"payload": {"field_a": 1, "field_b": 2}},
                ATTR_MANUFACTURER: manufacturer_code,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert device_command_mock.await_count == 1
    assert device_command_mock.await_args.args == (
        1,
        general.OnOff.cluster_id,
        0x99,
        CLUSTER_COMMAND_SERVER,
        None,
        {"payload": {"field_a": 1, "field_b": 2}},
    )
    assert device_command_mock.await_args.kwargs == {
        "cluster_type": CLUSTER_TYPE_IN,
        "manufacturer": manufacturer_code,
    }


async def test_issue_cluster_command_accepts_manufacturer_minus_one(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test command service validation accepts the legacy manufacturer=-1 sentinel."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    issue_command_mock = AsyncMock(return_value=None)
    with patch.object(zha_device, "issue_cluster_command", issue_command_mock):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_COMMAND: 1,
                ATTR_COMMAND_TYPE: CLUSTER_COMMAND_SERVER,
                ATTR_ARGS: [],
                ATTR_MANUFACTURER: -1,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )

    assert issue_command_mock.await_count == 1
    assert issue_command_mock.await_args.kwargs["manufacturer"] == -1


async def test_issue_cluster_command_rejects_manufacturer_above_uint16(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, hass_admin_user: MockUser
) -> None:
    """Test command service validation rejects manufacturer values above uint16."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_ISSUE_ZIGBEE_CLUSTER_COMMAND,
            {
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_ENDPOINT_ID: 1,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_COMMAND: 1,
                ATTR_COMMAND_TYPE: CLUSTER_COMMAND_SERVER,
                ATTR_ARGS: [],
                ATTR_MANUFACTURER: 0x10000,
            },
            blocking=True,
            context=Context(user_id=hass_admin_user.id),
        )


async def test_read_cluster_attribute_returns_typed_bool(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns bool values for bool-typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with patch.object(
        cluster, "read_attributes", AsyncMock(return_value=({attribute: 1}, {}))
    ):
        await zha_client.send_json(
            {
                ID: 15,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] is True


async def test_read_cluster_attribute_returns_typed_flag_names(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns list[str] values for flag-typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestFlags),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(
                return_value=(
                    {
                        attribute: _ServiceTestFlags.Option_A
                        | _ServiceTestFlags.Option_B
                    },
                    {},
                )
            ),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 16,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert sorted(msg["result"]) == ["Option A", "Option B"]


async def test_read_cluster_attribute_returns_typed_enum_label(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns enum member labels."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestEnum),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: _ServiceTestEnum.Option_B}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 17,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == "Option B"


async def test_read_cluster_attribute_uses_manufacturer_specific_attribute_type(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read converts values with manufacturer-specific attr definitions."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    manufacturer_code = 0x1234

    with (
        patch.dict(cluster.attributes, {attribute: MagicMock(type=zigpy.types.Bool)}),
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestEnum),
        ) as find_attribute_mock,
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: _ServiceTestEnum.Option_B}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 24,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_MANUFACTURER: manufacturer_code,
            }
        )

        msg = await zha_client.receive_json()

    find_attribute_mock.assert_called_once_with(
        attribute, manufacturer_code=manufacturer_code
    )
    assert msg["result"] == "Option B"


async def test_read_cluster_attribute_uses_manufacturer_specific_real_attr_type(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test read conversion uses real manufacturer-specific enum attr definitions."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id
    manufacturer_code = 0x1234
    real_attr_def = general.Ota.AttributeDefs.image_upgrade_status
    real_enum_type = real_attr_def.type

    with (
        patch.dict(cluster.attributes, {attribute: MagicMock(type=zigpy.types.Bool)}),
        patch.object(
            cluster, "find_attribute", return_value=real_attr_def
        ) as find_attribute_mock,
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: real_enum_type.Download_complete}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 27,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
                ATTR_MANUFACTURER: manufacturer_code,
            }
        )

        msg = await zha_client.receive_json()

    find_attribute_mock.assert_called_once_with(
        attribute, manufacturer_code=manufacturer_code
    )
    assert msg["result"] == "Download complete"


async def test_read_cluster_attribute_accepts_attribute_name_with_manufacturer(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read accepts attribute name plus manufacturer code."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute_name = general.OnOff.AttributeDefs.on_off.name
    manufacturer_code = 0x1234

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(
                id=general.OnOff.AttributeDefs.on_off.id,
                type=_ServiceTestEnum,
            ),
        ) as find_attribute_mock,
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(
                return_value=(
                    {attribute_name: _ServiceTestEnum.Option_B},
                    {},
                )
            ),
        ) as read_attributes_mock,
    ):
        await zha_client.send_json(
            {
                ID: 28,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute_name,
                ATTR_MANUFACTURER: manufacturer_code,
            }
        )

        msg = await zha_client.receive_json()

    find_attribute_mock.assert_called_once_with(
        attribute_name, manufacturer_code=manufacturer_code
    )
    assert read_attributes_mock.await_count == 1
    assert read_attributes_mock.await_args.args[0] == [attribute_name]
    assert read_attributes_mock.await_args.kwargs["manufacturer"] == manufacturer_code
    assert msg["result"] == "Option B"


async def test_read_cluster_attribute_name_request_handles_id_keyed_success_map(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read resolves values when zigpy returns values keyed by attr id."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute_name = general.OnOff.AttributeDefs.on_off.name
    attribute_id = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(
                id=attribute_id, name=attribute_name, type=_ServiceTestEnum
            ),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute_id: _ServiceTestEnum.Option_B}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 31,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute_name,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == "Option B"


async def test_read_cluster_attribute_rejects_manufacturer_minus_one(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read rejects the legacy manufacturer=-1 sentinel."""
    await zha_client.send_json(
        {
            ID: 26,
            TYPE: "zha/devices/clusters/attributes/value",
            ATTR_ENDPOINT_ID: 1,
            ATTR_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_CLUSTER_ID: general.OnOff.cluster_id,
            ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            ATTR_ATTRIBUTE: general.OnOff.AttributeDefs.on_off.name,
            ATTR_MANUFACTURER: -1,
        }
    )

    msg = await zha_client.receive_json()

    assert msg["success"] is False
    assert msg["error"]["code"] == ERR_INVALID_FORMAT


async def test_read_cluster_attribute_returns_typed_float(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns floats for float-typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=zigpy.types.Single),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: zigpy.types.Single(1.25)}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 18,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == pytest.approx(1.25)


async def test_read_cluster_attribute_returns_typed_bytes_hex(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns hex strings for bytes-typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=bytes),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: b"\x01\x02"}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 19,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == "0102"


async def test_read_cluster_attribute_returns_typed_serializable_bytes_hex(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns hex for serializable-bytes attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=zigpy.types.SerializableBytes),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(
                return_value=(
                    {attribute: zigpy.types.SerializableBytes(b"\x01\x02")},
                    {},
                )
            ),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 21,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == "0102"


@pytest.mark.parametrize(
    "conversion_error",
    [ValueError("boom"), TypeError("boom"), AttributeError("boom")],
)
async def test_read_cluster_attribute_logs_conversion_exception(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket, conversion_error: Exception
) -> None:
    """Test conversion failures are logged and still return string fallback."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestEnum),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: _ServiceTestEnum.Option_A}, {})),
        ),
        patch(
            "homeassistant.components.zha.websocket_api.attribute_value_to_form_value",
            side_effect=conversion_error,
        ),
        patch("homeassistant.components.zha.websocket_api._LOGGER.debug") as debug_log,
    ):
        await zha_client.send_json(
            {
                ID: 20,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == str(_ServiceTestEnum.Option_A)
    assert any(
        call_args.args
        and call_args.args[0].startswith("Failed to convert read attribute value")
        for call_args in debug_log.call_args_list
    )


async def test_read_cluster_attribute_find_attribute_lookup_failure_fallback(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read falls back to string when attr lookup fails."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(cluster, "find_attribute", side_effect=KeyError),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: _ServiceTestEnum.Option_A}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 29,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == str(_ServiceTestEnum.Option_A)


async def test_read_cluster_attribute_unknown_type_fallback(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read falls back to string when attr type is unknown."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster, "find_attribute", return_value=SimpleNamespace(type=None)
        ),
        patch.object(
            cluster, "read_attributes", AsyncMock(return_value=({attribute: 1}, {}))
        ),
    ):
        await zha_client.send_json(
            {
                ID: 30,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == "1"


async def test_read_cluster_attribute_returns_typed_struct_object(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns dict values for struct-typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestStruct),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(
                return_value=({attribute: _ServiceTestStruct(field_a=1, field_b=2)}, {})
            ),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 22,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == {"field_a": 1, "field_b": 2}


async def test_read_cluster_attribute_returns_typed_list_object(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read returns list values for list-like typed attributes."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with (
        patch.object(
            cluster,
            "find_attribute",
            return_value=MagicMock(type=_ServiceTestList),
        ),
        patch.object(
            cluster,
            "read_attributes",
            AsyncMock(return_value=({attribute: _ServiceTestList([1, 2, 3])}, {})),
        ),
    ):
        await zha_client.send_json(
            {
                ID: 23,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] == [{"value": 1}, {"value": 2}, {"value": 3}]


async def test_read_cluster_attribute_returns_null_when_no_value(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test websocket read contract returns null when no value is available."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )
    attribute = general.OnOff.AttributeDefs.on_off.id

    with patch.object(
        cluster,
        "read_attributes",
        AsyncMock(return_value=({}, {attribute: "unsupported"})),
    ):
        await zha_client.send_json(
            {
                ID: 25,
                TYPE: "zha/devices/clusters/attributes/value",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
                ATTR_ATTRIBUTE: attribute,
            }
        )

        msg = await zha_client.receive_json()

    assert msg["result"] is None


async def test_device_cluster_commands(zha_client: MockHAClientWebSocket) -> None:
    """Test getting device cluster commands."""
    await zha_client.send_json(
        {
            ID: 5,
            TYPE: "zha/devices/clusters/commands",
            ATTR_ENDPOINT_ID: 1,
            ATTR_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_CLUSTER_ID: 6,
            ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
        }
    )

    msg = await zha_client.receive_json()

    commands = msg["result"]
    assert commands

    for command in commands:
        assert "schema" in command
        assert "zcl_command" in command
        assert ID not in command
        assert ATTR_NAME not in command
        assert TYPE not in command
        zcl_command = command["zcl_command"]
        assert zcl_command[ID] is not None
        assert zcl_command[ATTR_NAME] is not None
        assert zcl_command["command_type"] in {
            "client",
            CLUSTER_COMMAND_SERVER,
        }
        assert "is_manufacturer_specific" in zcl_command
        assert zcl_command["is_manufacturer_specific"] in {True, False, None}
        assert "manufacturer_code" in zcl_command
        assert zcl_command["manufacturer_code"] is None or isinstance(
            zcl_command["manufacturer_code"], int
        )

    expected_commands = {
        (int(command_def.id), "client")
        for command_def in general.OnOff.ClientCommandDefs
    } | {
        (int(command_def.id), CLUSTER_COMMAND_SERVER)
        for command_def in general.OnOff.ServerCommandDefs
    }
    result_commands = {
        (entry["zcl_command"][ID], entry["zcl_command"]["command_type"])
        for entry in commands
    }
    assert expected_commands.issubset(result_commands)


async def test_device_cluster_commands_invalid_cluster_returns_error(
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test commands websocket returns an error for invalid cluster lookups."""
    await zha_client.send_json(
        {
            ID: 59,
            TYPE: "zha/devices/clusters/commands",
            ATTR_ENDPOINT_ID: 1,
            ATTR_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_CLUSTER_ID: 0xFFFF,
            ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
        }
    )

    msg = await zha_client.receive_json()

    assert msg["id"] == 59
    assert msg["success"] is False
    assert msg["error"]["code"] == ERR_UNKNOWN_ERROR


async def test_device_cluster_commands_manufacturer_metadata(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test command serialization keeps manufacturer metadata from command defs."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )

    manufacturer_code = 0x1234
    manufacturer_command = foundation.ZCLCommandDef(
        id=0x88,
        name="manufacturer_command",
        schema={"value": zigpy.types.uint8_t},
        direction=foundation.Direction.Server_to_Client,
        manufacturer_code=manufacturer_code,
    ).with_compiled_schema()

    with (
        patch.object(cluster, "ClientCommandDefs", []),
        patch.object(cluster, "ServerCommandDefs", [manufacturer_command]),
    ):
        await zha_client.send_json(
            {
                ID: 57,
                TYPE: "zha/devices/clusters/commands",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            }
        )

        msg = await zha_client.receive_json()

    commands = msg["result"]
    assert len(commands) == 1
    command = commands[0]["zcl_command"]
    assert command[ATTR_NAME] == "manufacturer_command"
    assert command["command_type"] == CLUSTER_COMMAND_SERVER
    assert command["is_manufacturer_specific"] is True
    assert command["manufacturer_code"] == manufacturer_code


async def test_device_cluster_commands_no_schema_returns_empty_schema(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test command entries with no schema serialize an empty schema list."""
    zha_device = get_zha_gateway(hass).get_device(EUI64.convert(IEEE_SWITCH_DEVICE))
    assert zha_device is not None
    cluster = zha_device.async_get_cluster(
        1, general.OnOff.cluster_id, cluster_type=CLUSTER_TYPE_IN
    )

    command_without_schema = SimpleNamespace(
        id=0x89,
        name="command_without_schema",
        schema=None,
        is_manufacturer_specific=False,
        manufacturer_code=None,
    )

    with (
        patch.object(cluster, "ClientCommandDefs", []),
        patch.object(cluster, "ServerCommandDefs", [command_without_schema]),
    ):
        await zha_client.send_json(
            {
                ID: 58,
                TYPE: "zha/devices/clusters/commands",
                ATTR_ENDPOINT_ID: 1,
                ATTR_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_CLUSTER_ID: general.OnOff.cluster_id,
                ATTR_CLUSTER_TYPE: CLUSTER_TYPE_IN,
            }
        )

        msg = await zha_client.receive_json()

    command = msg["result"][0]
    assert command["schema"] == []
    assert command["zcl_command"][ATTR_NAME] == "command_without_schema"


@pytest.mark.freeze_time("2023-09-23 20:16:00+00:00")
async def test_list_devices(zha_client: MockHAClientWebSocket) -> None:
    """Test getting ZHA devices."""
    await zha_client.send_json({ID: 5, TYPE: "zha/devices"})

    msg = await zha_client.receive_json()

    devices = msg["result"]
    assert len(devices) == 3  # the coordinator is included as well

    msg_id = 100
    for device in devices:
        msg_id += 1
        assert device[ATTR_IEEE] is not None
        assert device[ATTR_MANUFACTURER] is not None
        assert device[ATTR_MODEL] is not None
        assert device[ATTR_NAME] is not None
        assert device[ATTR_QUIRK_APPLIED] is not None
        assert device["entities"] is not None
        assert device[ATTR_NEIGHBORS] is not None
        assert device[ATTR_ENDPOINT_NAMES] is not None

        for entity_reference in device["entities"]:
            assert entity_reference[ATTR_NAME] is not None
            assert entity_reference["entity_id"] is not None

        await zha_client.send_json(
            {ID: msg_id, TYPE: "zha/device", ATTR_IEEE: device[ATTR_IEEE]}
        )
        msg = await zha_client.receive_json()
        device2 = msg["result"]
        assert device == device2


async def test_device_info_area(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_device_mock: Callable[..., Device],
) -> None:
    """Test the device info area_id reflects the registry device's effective area.

    ZHA registers all its devices as top-level (never via ``parent_device_id``),
    so a device's effective area equals its own ``area_id``.
    """
    await setup_zha()
    gateway = get_zha_gateway(hass)
    gateway_proxy: ZHAGatewayProxy = get_zha_gateway_proxy(hass)

    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [general.OnOff.cluster_id, general.Basic.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.ON_OFF_SWITCH,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
        ieee=IEEE_SWITCH_DEVICE,
    )

    gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    zha_device_proxy: ZHADeviceProxy = gateway_proxy.get_device_proxy(zigpy_device.ieee)

    assert zha_device_proxy.zha_device_info[ATTR_AREA_ID] is None

    device_registry.async_update_device(zha_device_proxy.device_id, area_id="12345A")

    assert zha_device_proxy.zha_device_info[ATTR_AREA_ID] == "12345A"


async def test_get_zha_config(zha_client: MockHAClientWebSocket) -> None:
    """Test getting ZHA custom configuration."""
    await zha_client.send_json({ID: 5, TYPE: "zha/configuration"})

    msg = await zha_client.receive_json()

    configuration = msg["result"]
    assert configuration == BASE_CUSTOM_CONFIGURATION


async def test_get_zha_config_with_alarm(
    hass: HomeAssistant,
    zha_client: MockHAClientWebSocket,
    zigpy_device_mock: Callable[..., Device],
) -> None:
    """Test getting ZHA custom configuration."""

    gateway = get_zha_gateway(hass)
    gateway_proxy: ZHAGatewayProxy = get_zha_gateway_proxy(hass)

    zigpy_device_ias = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [security.IasAce.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.IAS_ANCILLARY_CONTROL,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
    )

    gateway.get_or_create_device(zigpy_device_ias)
    await gateway.async_device_initialized(zigpy_device_ias)
    await hass.async_block_till_done(wait_background_tasks=True)
    zha_device_proxy: ZHADeviceProxy = gateway_proxy.get_device_proxy(
        zigpy_device_ias.ieee
    )

    await zha_client.send_json({ID: 5, TYPE: "zha/configuration"})

    msg = await zha_client.receive_json()

    configuration = msg["result"]
    assert configuration == CONFIG_WITH_ALARM_OPTIONS

    # test that the alarm options are not in the config when we remove the device
    zha_device_proxy.gateway_proxy.gateway.device_removed(zha_device_proxy.device)
    await hass.async_block_till_done()
    await zha_client.send_json({ID: 6, TYPE: "zha/configuration"})

    msg = await zha_client.receive_json()

    configuration = msg["result"]
    assert configuration == BASE_CUSTOM_CONFIGURATION


async def test_update_zha_config(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    zha_client: MockHAClientWebSocket,
    app_controller: ControllerApplication,
) -> None:
    """Test updating ZHA custom configuration."""
    configuration: dict = deepcopy(BASE_CUSTOM_CONFIGURATION)
    configuration["data"]["zha_options"]["default_light_transition"] = 10

    with patch(
        "bellows.zigbee.application.ControllerApplication.new",
        return_value=app_controller,
    ):
        await zha_client.send_json(
            {ID: 5, TYPE: "zha/configuration/update", "data": configuration["data"]}
        )
        msg = await zha_client.receive_json()
        assert msg["success"]

    await zha_client.send_json({ID: 6, TYPE: "zha/configuration"})
    msg = await zha_client.receive_json()
    test_configuration = msg["result"]
    assert test_configuration == configuration

    await hass.config_entries.async_unload(config_entry.entry_id)


async def test_device_not_found(zha_client: MockHAClientWebSocket) -> None:
    """Test not found response from get device API."""
    await zha_client.send_json(
        {ID: 6, TYPE: "zha/device", ATTR_IEEE: "28:6d:97:00:01:04:11:8c"}
    )
    msg = await zha_client.receive_json()
    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert not msg["success"]
    assert msg["error"]["code"] == ERR_NOT_FOUND


async def test_list_groups(zha_client: MockHAClientWebSocket) -> None:
    """Test getting ZHA zigbee groups."""
    await zha_client.send_json({ID: 7, TYPE: "zha/groups"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 7
    assert msg["type"] == TYPE_RESULT

    groups = msg["result"]
    assert len(groups) == 1

    for group in groups:
        assert group["group_id"] == FIXTURE_GRP_ID
        assert group["name"] == FIXTURE_GRP_NAME
        assert group["members"] == []


async def test_get_group(zha_client: MockHAClientWebSocket) -> None:
    """Test getting a specific ZHA zigbee group."""
    await zha_client.send_json({ID: 8, TYPE: "zha/group", GROUP_ID: FIXTURE_GRP_ID})

    msg = await zha_client.receive_json()
    assert msg["id"] == 8
    assert msg["type"] == TYPE_RESULT

    group = msg["result"]
    assert group is not None
    assert group["group_id"] == FIXTURE_GRP_ID
    assert group["name"] == FIXTURE_GRP_NAME
    assert group["members"] == []


async def test_get_group_not_found(zha_client: MockHAClientWebSocket) -> None:
    """Test not found response from get group API."""
    await zha_client.send_json({ID: 9, TYPE: "zha/group", GROUP_ID: 1_234_567})

    msg = await zha_client.receive_json()

    assert msg["id"] == 9
    assert msg["type"] == TYPE_RESULT
    assert not msg["success"]
    assert msg["error"]["code"] == ERR_NOT_FOUND


async def test_list_groupable_devices(
    hass: HomeAssistant,
    zha_client: MockHAClientWebSocket,
    zigpy_app_controller: ControllerApplication,
) -> None:
    """Test getting ZHA devices that have a group cluster."""
    # Ensure the coordinator doesn't have a group cluster
    coordinator = zigpy_app_controller.get_device(nwk=0x0000)

    del coordinator.endpoints[1].in_clusters[Groups.cluster_id]

    await zha_client.send_json({ID: 10, TYPE: "zha/devices/groupable"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 10
    assert msg["type"] == TYPE_RESULT

    device_endpoints = msg["result"]
    assert len(device_endpoints) == 1

    for endpoint in device_endpoints:
        assert endpoint["device"][ATTR_IEEE] == "01:2d:6f:00:0a:90:69:e8"
        assert endpoint["device"][ATTR_MANUFACTURER] is not None
        assert endpoint["device"][ATTR_MODEL] is not None
        assert endpoint["device"][ATTR_NAME] is not None
        assert endpoint["device"][ATTR_QUIRK_APPLIED] is not None
        assert endpoint["device"]["entities"] is not None
        assert endpoint["endpoint_id"] is not None
        assert endpoint["entities"] is not None

        for entity_reference in endpoint["device"]["entities"]:
            assert entity_reference[ATTR_NAME] is not None
            assert entity_reference["entity_id"] is not None

        if len(endpoint["entities"]) == 1:
            assert endpoint["entities"][0]["original_name"] is None
        else:
            for entity_reference in endpoint["entities"]:
                assert entity_reference["original_name"] is not None

    # Make sure there are no groupable devices when the device is unavailable
    # Make device unavailable
    get_zha_gateway_proxy(hass).device_proxies[
        EUI64.convert(IEEE_GROUPABLE_DEVICE)
    ].device.available = False
    await hass.async_block_till_done(wait_background_tasks=True)

    await zha_client.send_json({ID: 11, TYPE: "zha/devices/groupable"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 11
    assert msg["type"] == TYPE_RESULT

    device_endpoints = msg["result"]
    assert len(device_endpoints) == 0


async def test_add_group(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test adding and getting a new ZHA zigbee group."""
    await zha_client.send_json(
        {
            ID: 12,
            TYPE: "zha/group/add",
            GROUP_NAME: "new_group",
            "members": [{"ieee": IEEE_GROUPABLE_DEVICE, "endpoint_id": 1}],
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 12
    assert msg["type"] == TYPE_RESULT

    added_group = msg["result"]

    groupable_device = get_zha_gateway_proxy(hass).device_proxies[
        EUI64.convert(IEEE_GROUPABLE_DEVICE)
    ]

    assert added_group["name"] == "new_group"
    assert len(added_group["members"]) == 1
    assert added_group["members"][0]["device"]["ieee"] == IEEE_GROUPABLE_DEVICE
    assert (
        added_group["members"][0]["device"]["device_reg_id"]
        == groupable_device.device_id
    )

    await zha_client.send_json({ID: 13, TYPE: "zha/groups"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 13
    assert msg["type"] == TYPE_RESULT

    groups = msg["result"]
    assert len(groups) == 2

    for group in groups:
        assert group["name"] == FIXTURE_GRP_NAME or group["name"] == "new_group"


async def test_remove_group(zha_client: MockHAClientWebSocket) -> None:
    """Test removing a new ZHA zigbee group."""

    await zha_client.send_json({ID: 14, TYPE: "zha/groups"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 14
    assert msg["type"] == TYPE_RESULT

    groups = msg["result"]
    assert len(groups) == 1

    await zha_client.send_json(
        {ID: 15, TYPE: "zha/group/remove", GROUP_IDS: [FIXTURE_GRP_ID]}
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 15
    assert msg["type"] == TYPE_RESULT

    groups_remaining = msg["result"]
    assert len(groups_remaining) == 0

    await zha_client.send_json({ID: 16, TYPE: "zha/groups"})

    msg = await zha_client.receive_json()
    assert msg["id"] == 16
    assert msg["type"] == TYPE_RESULT

    groups = msg["result"]
    assert len(groups) == 0


async def test_add_group_member(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test adding a ZHA zigbee group member."""
    await zha_client.send_json(
        {
            ID: 12,
            TYPE: "zha/group/add",
            GROUP_NAME: "new_group",
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 12
    assert msg["type"] == TYPE_RESULT

    added_group = msg["result"]

    assert len(added_group["members"]) == 0

    await zha_client.send_json(
        {
            ID: 13,
            TYPE: "zha/group/members/add",
            GROUP_ID: added_group["group_id"],
            "members": [{"ieee": IEEE_GROUPABLE_DEVICE, "endpoint_id": 1}],
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 13
    assert msg["type"] == TYPE_RESULT

    added_group = msg["result"]

    assert len(added_group["members"]) == 1
    assert added_group["name"] == "new_group"
    assert added_group["members"][0]["device"]["ieee"] == IEEE_GROUPABLE_DEVICE


async def test_remove_group_member(
    hass: HomeAssistant, zha_client: MockHAClientWebSocket
) -> None:
    """Test removing a ZHA zigbee group member."""
    await zha_client.send_json(
        {
            ID: 12,
            TYPE: "zha/group/add",
            GROUP_NAME: "new_group",
            "members": [{"ieee": IEEE_GROUPABLE_DEVICE, "endpoint_id": 1}],
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 12
    assert msg["type"] == TYPE_RESULT

    added_group = msg["result"]

    assert added_group["name"] == "new_group"
    assert len(added_group["members"]) == 1
    assert added_group["members"][0]["device"]["ieee"] == IEEE_GROUPABLE_DEVICE

    await zha_client.send_json(
        {
            ID: 13,
            TYPE: "zha/group/members/remove",
            GROUP_ID: added_group["group_id"],
            "members": [{"ieee": IEEE_GROUPABLE_DEVICE, "endpoint_id": 1}],
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 13
    assert msg["type"] == TYPE_RESULT

    added_group = msg["result"]
    assert len(added_group["members"]) == 0


@pytest.fixture
async def app_controller(
    hass: HomeAssistant,
    setup_zha: Callable[..., Coroutine[None]],
    zigpy_app_controller: ControllerApplication,
) -> ControllerApplication:
    """Fixture for zigpy Application Controller."""
    await setup_zha()
    zigpy_app_controller.permit.reset_mock()
    return zigpy_app_controller


@pytest.mark.parametrize(
    ("params", "duration", "node"),
    [
        ({}, 60, None),
        ({ATTR_DURATION: 30}, 30, None),
        (
            {ATTR_DURATION: 33, ATTR_IEEE: "aa:bb:cc:dd:aa:bb:cc:dd"},
            33,
            zigpy.types.EUI64.convert("aa:bb:cc:dd:aa:bb:cc:dd"),
        ),
        (
            {ATTR_IEEE: "aa:bb:cc:dd:aa:bb:cc:d1"},
            60,
            zigpy.types.EUI64.convert("aa:bb:cc:dd:aa:bb:cc:d1"),
        ),
    ],
)
async def test_permit_ha12(
    hass: HomeAssistant,
    app_controller: ControllerApplication,
    hass_admin_user: MockUser,
    params: dict[str, str | int],
    duration: int,
    node: EUI64 | None,
) -> None:
    """Test permit service."""

    await hass.services.async_call(
        DOMAIN, SERVICE_PERMIT, params, True, Context(user_id=hass_admin_user.id)
    )
    assert app_controller.permit.await_count == 1
    assert app_controller.permit.await_args[1]["time_s"] == duration
    assert app_controller.permit.await_args[1]["node"] == node
    assert app_controller.permit_with_link_key.call_count == 0


IC_TEST_PARAMS = (
    (
        {
            ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_INSTALL_CODE: "5279-7BF4-A508-4DAA-8E17-12B6-1741-CA02-4051",
        },
        zigpy.types.EUI64.convert(IEEE_SWITCH_DEVICE),
        zigpy.util.convert_install_code(
            unhexlify("52797BF4A5084DAA8E1712B61741CA024051")
        ),
    ),
    (
        {
            ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
            ATTR_INSTALL_CODE: "52797BF4A5084DAA8E1712B61741CA024051",
        },
        zigpy.types.EUI64.convert(IEEE_SWITCH_DEVICE),
        zigpy.util.convert_install_code(
            unhexlify("52797BF4A5084DAA8E1712B61741CA024051")
        ),
    ),
)


@pytest.mark.parametrize(("params", "src_ieee", "code"), IC_TEST_PARAMS)
async def test_permit_with_install_code(
    hass: HomeAssistant,
    app_controller: ControllerApplication,
    hass_admin_user: MockUser,
    params: dict[str, str | int],
    src_ieee: EUI64,
    code: zigpy.types.KeyData,
) -> None:
    """Test permit service with install code."""

    await hass.services.async_call(
        DOMAIN, SERVICE_PERMIT, params, True, Context(user_id=hass_admin_user.id)
    )
    assert app_controller.permit.await_count == 0
    assert app_controller.permit_with_link_key.call_count == 1
    assert app_controller.permit_with_link_key.await_args[1]["time_s"] == 60
    assert app_controller.permit_with_link_key.await_args[1]["node"] == src_ieee
    assert app_controller.permit_with_link_key.await_args[1]["link_key"] == code


IC_FAIL_PARAMS = (
    {
        # wrong install code
        ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
        ATTR_INSTALL_CODE: "5279-7BF4-A508-4DAA-8E17-12B6-1741-CA02-4052",
    },
    # incorrect service params
    {ATTR_INSTALL_CODE: "5279-7BF4-A508-4DAA-8E17-12B6-1741-CA02-4051"},
    {ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE},
    {
        # incorrect service params
        ATTR_INSTALL_CODE: "5279-7BF4-A508-4DAA-8E17-12B6-1741-CA02-4051",
        ATTR_QR_CODE: "Z:000D6FFFFED4163B$I:52797BF4A5084DAA8E1712B61741CA024051",
    },
    {
        # incorrect service params
        ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
        ATTR_QR_CODE: "Z:000D6FFFFED4163B$I:52797BF4A5084DAA8E1712B61741CA024051",
    },
    {
        # good regex match, but bad code
        ATTR_QR_CODE: "Z:000D6FFFFED4163B$I:52797BF4A5084DAA8E1712B61741CA024052"
    },
    {
        # good aqara regex match, but bad code
        ATTR_QR_CODE: (
            "G$M:751$S:357S00001579$D:000000000F350FFD%Z$A:04CF8CDF"
            "3C3C3C3C$I:52797BF4A5084DAA8E1712B61741CA024052"
        )
    },
    # good consciot regex match, but bad code
    {ATTR_QR_CODE: "000D6FFFFED4163B|52797BF4A5084DAA8E1712B61741CA024052"},
)


@pytest.mark.parametrize("params", IC_FAIL_PARAMS)
async def test_permit_with_install_code_fail(
    hass: HomeAssistant,
    app_controller: ControllerApplication,
    hass_admin_user: MockUser,
    params: dict[str, str | int],
) -> None:
    """Test permit service with install code."""

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, SERVICE_PERMIT, params, True, Context(user_id=hass_admin_user.id)
        )
    assert app_controller.permit.await_count == 0
    assert app_controller.permit_with_link_key.call_count == 0


IC_QR_CODE_TEST_PARAMS = (
    (
        {ATTR_QR_CODE: "000D6FFFFED4163B|52797BF4A5084DAA8E1712B61741CA024051"},
        zigpy.types.EUI64.convert("00:0D:6F:FF:FE:D4:16:3B"),
        zigpy.util.convert_install_code(
            unhexlify("52797BF4A5084DAA8E1712B61741CA024051")
        ),
    ),
    (
        {ATTR_QR_CODE: "Z:000D6FFFFED4163B$I:52797BF4A5084DAA8E1712B61741CA024051"},
        zigpy.types.EUI64.convert("00:0D:6F:FF:FE:D4:16:3B"),
        zigpy.util.convert_install_code(
            unhexlify("52797BF4A5084DAA8E1712B61741CA024051")
        ),
    ),
    (
        {
            ATTR_QR_CODE: (
                "G$M:751$S:357S00001579$D:000000000F350FFD%Z$A:04CF8CDF"
                "3C3C3C3C$I:52797BF4A5084DAA8E1712B61741CA024051"
            )
        },
        zigpy.types.EUI64.convert("04:CF:8C:DF:3C:3C:3C:3C"),
        zigpy.util.convert_install_code(
            unhexlify("52797BF4A5084DAA8E1712B61741CA024051")
        ),
    ),
    (
        {
            ATTR_QR_CODE: (
                "RB01SG"
                "0D836591B3CC0010000000000000000000"
                "000D6F0019107BB1"
                "DLK"
                "E4636CB6C41617C3E08F7325FFBFE1F9"
            )
        },
        zigpy.types.EUI64.convert("00:0D:6F:00:19:10:7B:B1"),
        zigpy.types.KeyData.convert("E4:63:6C:B6:C4:16:17:C3:E0:8F:73:25:FF:BF:E1:F9"),
    ),
)


@pytest.mark.parametrize(("params", "src_ieee", "code"), IC_QR_CODE_TEST_PARAMS)
async def test_permit_with_qr_code(
    hass: HomeAssistant,
    app_controller: ControllerApplication,
    hass_admin_user: MockUser,
    params: dict[str, str | int],
    src_ieee: EUI64,
    code: zigpy.types.KeyData,
) -> None:
    """Test permit service with install code from qr code."""

    await hass.services.async_call(
        DOMAIN, SERVICE_PERMIT, params, True, Context(user_id=hass_admin_user.id)
    )
    assert app_controller.permit.await_count == 0
    assert app_controller.permit_with_link_key.call_count == 1
    assert app_controller.permit_with_link_key.await_args[1]["time_s"] == 60
    assert app_controller.permit_with_link_key.await_args[1]["node"] == src_ieee
    assert app_controller.permit_with_link_key.await_args[1]["link_key"] == code


@pytest.mark.parametrize(("params", "src_ieee", "code"), IC_QR_CODE_TEST_PARAMS)
async def test_ws_permit_with_qr_code(
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
    params: dict[str, str | int],
    src_ieee: EUI64,
    code: zigpy.types.KeyData,
) -> None:
    """Test permit service with install code from qr code."""

    await zha_client.send_json(
        {ID: 14, TYPE: f"{DOMAIN}/devices/{SERVICE_PERMIT}", **params}
    )

    msg_type = None
    while msg_type != TYPE_RESULT:
        # There will be logging events coming over the websocket
        # as well so we want to ignore those
        msg = await zha_client.receive_json()
        msg_type = msg["type"]

    assert msg["id"] == 14
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]

    assert app_controller.permit.await_count == 0
    assert app_controller.permit_with_link_key.call_count == 1
    assert app_controller.permit_with_link_key.await_args[1]["time_s"] == 60
    assert app_controller.permit_with_link_key.await_args[1]["node"] == src_ieee
    assert app_controller.permit_with_link_key.await_args[1]["link_key"] == code


@pytest.mark.parametrize("params", IC_FAIL_PARAMS)
async def test_ws_permit_with_install_code_fail(
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
    params: dict[str, str | int],
) -> None:
    """Test permit ws service with install code."""

    await zha_client.send_json(
        {ID: 14, TYPE: f"{DOMAIN}/devices/{SERVICE_PERMIT}", **params}
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 14
    assert msg["type"] == TYPE_RESULT
    assert msg["success"] is False

    assert app_controller.permit.await_count == 0
    assert app_controller.permit_with_link_key.call_count == 0


@pytest.mark.parametrize(
    ("params", "duration", "node"),
    [
        ({}, 60, None),
        ({ATTR_DURATION: 30}, 30, None),
        (
            {ATTR_DURATION: 33, ATTR_IEEE: "aa:bb:cc:dd:aa:bb:cc:dd"},
            33,
            zigpy.types.EUI64.convert("aa:bb:cc:dd:aa:bb:cc:dd"),
        ),
        (
            {ATTR_IEEE: "aa:bb:cc:dd:aa:bb:cc:d1"},
            60,
            zigpy.types.EUI64.convert("aa:bb:cc:dd:aa:bb:cc:d1"),
        ),
    ],
)
async def test_ws_permit_ha12(
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
    params: dict[str, str | int],
    duration: int,
    node: EUI64 | None,
) -> None:
    """Test permit ws service."""

    await zha_client.send_json(
        {ID: 14, TYPE: f"{DOMAIN}/devices/{SERVICE_PERMIT}", **params}
    )

    msg_type = None
    while msg_type != TYPE_RESULT:
        # There will be logging events coming over the websocket
        # as well so we want to ignore those
        msg = await zha_client.receive_json()
        msg_type = msg["type"]

    assert msg["id"] == 14
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]

    assert app_controller.permit.await_count == 1
    assert app_controller.permit.await_args[1]["time_s"] == duration
    assert app_controller.permit.await_args[1]["node"] == node
    assert app_controller.permit_with_link_key.call_count == 0


async def test_get_network_settings(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test current network settings are returned."""

    await app_controller.backups.create_backup()

    await zha_client.send_json({ID: 6, TYPE: f"{DOMAIN}/network/settings"})
    msg = await zha_client.receive_json()

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]
    assert "radio_type" in msg["result"]
    assert "network_info" in msg["result"]["settings"]
    assert "path" in msg["result"]["device"]


async def test_list_network_backups(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test backups are serialized."""

    await app_controller.backups.create_backup()

    await zha_client.send_json({ID: 6, TYPE: f"{DOMAIN}/network/backups/list"})
    msg = await zha_client.receive_json()

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]
    assert "network_info" in msg["result"][0]


async def test_create_network_backup(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test creating backup."""

    assert not app_controller.backups.backups
    await zha_client.send_json({ID: 6, TYPE: f"{DOMAIN}/network/backups/create"})
    msg = await zha_client.receive_json()
    assert len(app_controller.backups.backups) == 1

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]
    assert "backup" in msg["result"] and "is_complete" in msg["result"]


async def test_restore_network_backup_success(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test successfully restoring a backup."""

    backup = zigpy.backups.NetworkBackup()

    with patch.object(app_controller.backups, "restore_backup", new=AsyncMock()) as p:
        await zha_client.send_json(
            {
                ID: 6,
                TYPE: f"{DOMAIN}/network/backups/restore",
                "backup": backup.as_dict(),
            }
        )
        msg = await zha_client.receive_json()

    p.assert_called_once_with(backup)
    assert "ezsp" not in backup.network_info.stack_specific

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]


async def test_restore_network_backup_force_write_eui64(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test successfully restoring a backup."""

    backup = zigpy.backups.NetworkBackup()

    with patch.object(app_controller.backups, "restore_backup", new=AsyncMock()) as p:
        await zha_client.send_json(
            {
                ID: 6,
                TYPE: f"{DOMAIN}/network/backups/restore",
                "backup": backup.as_dict(),
                "ezsp_force_write_eui64": True,
            }
        )
        msg = await zha_client.receive_json()

    # EUI64 will be overwritten
    p.assert_called_once_with(
        backup.replace(
            network_info=backup.network_info.replace(
                stack_specific={"ezsp": {EZSP_OVERWRITE_EUI64: True}}
            )
        )
    )

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]


@patch("zigpy.backups.NetworkBackup.from_dict", new=lambda v: v)
async def test_restore_network_backup_failure(
    app_controller: ControllerApplication, zha_client: MockHAClientWebSocket
) -> None:
    """Test successfully restoring a backup."""

    with patch.object(
        app_controller.backups,
        "restore_backup",
        new=AsyncMock(side_effect=ValueError("Restore failed")),
    ) as p:
        await zha_client.send_json(
            {ID: 6, TYPE: f"{DOMAIN}/network/backups/restore", "backup": "a backup"}
        )
        msg = await zha_client.receive_json()

    p.assert_called_once_with("a backup")

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert not msg["success"]
    assert msg["error"]["code"] == ERR_INVALID_FORMAT


@pytest.mark.parametrize("new_channel", ["auto", 15])
async def test_websocket_change_channel(
    new_channel: int | str,
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test websocket API to migrate the network to a new channel."""

    with patch(
        "homeassistant.components.zha.websocket_api.async_change_channel",
        autospec=True,
    ) as change_channel_mock:
        await zha_client.send_json(
            {
                ID: 6,
                TYPE: f"{DOMAIN}/network/change_channel",
                "new_channel": new_channel,
            }
        )
        msg = await zha_client.receive_json()

    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]

    change_channel_mock.assert_has_calls([call(ANY, new_channel)])


@pytest.mark.parametrize(
    "operation",
    [("bind", zdo_types.ZDOCmd.Bind_req), ("unbind", zdo_types.ZDOCmd.Unbind_req)],
)
async def test_websocket_bind_unbind_devices(
    operation: tuple[str, zdo_types.ZDOCmd],
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test websocket API for binding and unbinding devices to devices."""

    command_type, req = operation
    with patch(
        "homeassistant.components.zha.websocket_api.async_binding_operation",
        autospec=True,
    ) as binding_operation_mock:
        await zha_client.send_json(
            {
                ID: 27,
                TYPE: f"zha/devices/{command_type}",
                ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
                ATTR_TARGET_IEEE: IEEE_GROUPABLE_DEVICE,
            }
        )
        msg = await zha_client.receive_json()

    assert msg["id"] == 27
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]
    assert binding_operation_mock.mock_calls == [
        call(
            ANY,
            EUI64.convert(IEEE_SWITCH_DEVICE),
            EUI64.convert(IEEE_GROUPABLE_DEVICE),
            req,
        )
    ]


@pytest.mark.parametrize("command_type", ["bind", "unbind"])
async def test_websocket_bind_unbind_group(
    command_type: str,
    hass: HomeAssistant,
    app_controller: ControllerApplication,
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test websocket API for binding and unbinding devices to groups."""

    test_group_id = 0x0001
    gateway_mock = MagicMock()

    with patch(
        "homeassistant.components.zha.websocket_api.get_zha_gateway",
        return_value=gateway_mock,
    ):
        device_mock = MagicMock()
        bind_mock = AsyncMock()
        unbind_mock = AsyncMock()
        device_mock.async_bind_to_group = bind_mock
        device_mock.async_unbind_from_group = unbind_mock
        gateway_mock.get_device = MagicMock()
        gateway_mock.get_device.return_value = device_mock
        await zha_client.send_json(
            {
                ID: 27,
                TYPE: f"zha/groups/{command_type}",
                ATTR_SOURCE_IEEE: IEEE_SWITCH_DEVICE,
                GROUP_ID: test_group_id,
                BINDINGS: [
                    {
                        ATTR_ENDPOINT_ID: 1,
                        ID: 6,
                        ATTR_NAME: "OnOff",
                        ATTR_TYPE: "out",
                    },
                ],
            }
        )
        msg = await zha_client.receive_json()

    assert msg["id"] == 27
    assert msg["type"] == TYPE_RESULT
    assert msg["success"]
    if command_type == "bind":
        assert bind_mock.mock_calls == [call(test_group_id, ANY)]
    elif command_type == "unbind":
        assert unbind_mock.mock_calls == [call(test_group_id, ANY)]


async def test_websocket_reconfigure(
    hass: HomeAssistant,
    zha_client: MockHAClientWebSocket,
    zigpy_device_mock: Callable[..., Device],
) -> None:
    """Test websocket API to re-interview a device."""
    gateway = get_zha_gateway(hass)
    zigpy_device = zigpy_device_mock(
        {
            1: {
                SIG_EP_INPUT: [closures.WindowCovering.cluster_id],
                SIG_EP_OUTPUT: [],
                SIG_EP_TYPE: zigpy.profiles.zha.DeviceType.SHADE,
                SIG_EP_PROFILE: zigpy.profiles.zha.PROFILE_ID,
            }
        },
    )

    zha_device = gateway.get_or_create_device(zigpy_device)
    await gateway.async_device_initialized(zigpy_device)
    await hass.async_block_till_done(wait_background_tasks=True)

    zha_device_proxy = get_zha_gateway_proxy(hass).get_device_proxy(zha_device.ieee)

    async def mock_reinterview(ieee: EUI64) -> None:
        zha_device_proxy.handle_zha_cluster_configure_reporting(
            ClusterConfigureReportingEvent(
                device_ieee=zha_device_proxy.device.ieee,
                endpoint_id=1,
                cluster_id=258,
                cluster_name="Window Covering",
                attributes={
                    "current_position_lift_percentage": {
                        "min": 0,
                        "max": 900,
                        "id": "current_position_lift_percentage",
                        "name": "current_position_lift_percentage",
                        "change": 1,
                        "status": "SUCCESS",
                    },
                    "current_position_tilt_percentage": {
                        "min": 0,
                        "max": 900,
                        "id": "current_position_tilt_percentage",
                        "name": "current_position_tilt_percentage",
                        "change": 1,
                        "status": "SUCCESS",
                    },
                },
            )
        )

        zha_device_proxy.handle_zha_cluster_bind(
            ClusterBindEvent(
                device_ieee=zha_device_proxy.device.ieee,
                endpoint_id=1,
                cluster_id=1,
                cluster_name="Window Covering",
                success=True,
            )
        )

        zha_device_proxy.handle_zha_device_configured(
            DeviceConfiguredEvent(device_ieee=zha_device_proxy.device.ieee)
        )

    with patch.object(
        gateway, "async_reinterview_device", side_effect=mock_reinterview
    ) as reinterview_mock:
        await zha_client.send_json(
            {
                ID: 6,
                TYPE: "zha/devices/reconfigure",
                ATTR_IEEE: str(zha_device_proxy.device.ieee),
            }
        )

        messages = []

        while len(messages) != 3:
            msg = await zha_client.receive_json()

            if msg[ID] == 6:
                messages.append(msg)

    # Ensure the gateway re-interview was triggered with the correct IEEE
    assert reinterview_mock.mock_calls == [call(zha_device_proxy.device.ieee)]

    # Ensure the frontend receives progress events
    assert {m["event"]["type"] for m in messages} == {
        "zha_channel_configure_reporting",
        "zha_channel_bind",
        "zha_channel_cfg_done",
    }


async def test_websocket_reconfigure_device_not_found(
    zha_client: MockHAClientWebSocket,
) -> None:
    """Test websocket reconfigure returns ERR_NOT_FOUND for an unknown IEEE."""
    await zha_client.send_json(
        {
            ID: 6,
            TYPE: "zha/devices/reconfigure",
            ATTR_IEEE: "28:6d:97:00:01:04:11:8c",
        }
    )

    msg = await zha_client.receive_json()
    assert msg["id"] == 6
    assert msg["type"] == TYPE_RESULT
    assert not msg["success"]
    assert msg["error"]["code"] == ERR_NOT_FOUND
