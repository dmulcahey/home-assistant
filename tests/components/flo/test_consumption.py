"""Test isolation and freshness of optional Flo consumption."""

import asyncio
from datetime import timedelta
from http import HTTPStatus
import math
from unittest.mock import patch

from aioflo.errors import RequestError
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_load_json_object_fixture,
)
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.homeassistant import (
    DOMAIN as HOMEASSISTANT_DOMAIN,
    SERVICE_UPDATE_ENTITY,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

USAGE = "sensor.smart_water_shutoff_today_s_water_usage"
MODE = "sensor.smart_water_shutoff_current_system_mode"


async def async_refresh_entity(hass: HomeAssistant, entity_id: str) -> None:
    """Request a user-visible manual entity refresh."""
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    await hass.services.async_call(
        HOMEASSISTANT_DOMAIN,
        SERVICE_UPDATE_ENTITY,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize("error", [RequestError("offline"), TimeoutError()])
async def test_optional_startup_failure(
    hass: HomeAssistant, config_entry: MockConfigEntry, error: Exception
) -> None:
    """Optional failure must not prevent live entities and controls loading."""
    config_entry.add_to_hass(hass)
    with patch("aioflo.water.Water.get_consumption_info", side_effect=error) as water:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_awaited_once()
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"
    assert (
        hass.states.get("sensor.kitchen_sink_temperature").state == "16.1111111111111"
    )
    assert hass.states.get("switch.smart_water_shutoff_shutoff_valve").state == "on"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_metadata_required(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """First startup retries if required device metadata is unavailable."""
    config_entry.add_to_hass(hass)
    with patch("aioflo.device.Device.get_info", side_effect=RequestError("offline")):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.states.async_all()


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize("total", [None, True, -1, math.nan, math.inf, "0"])
async def test_invalid_total_preserves_last_good(
    hass: HomeAssistant, config_entry: MockConfigEntry, total: object
) -> None:
    """Invalid totals must not overwrite a previously successful sample."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device = config_entry.runtime_data.devices[0]
    consumption = device
    previous = consumption._water_usage
    with patch(
        "aioflo.water.Water.get_consumption_info",
        return_value={"aggregations": {"sumTotalGallonsConsumed": total}},
    ):
        await async_refresh_entity(hass, USAGE)
    assert consumption._water_usage == previous
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_failure_recovery_manual_refresh(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Consumption failures stay isolated and a manual refresh recovers zero."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    consumption = config_entry.runtime_data.devices[0]
    previous = consumption._water_usage
    with patch(
        "aioflo.water.Water.get_consumption_info", side_effect=RequestError("offline")
    ):
        for _ in range(4):
            freezer.tick(timedelta(seconds=65))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
    assert consumption._water_usage == previous
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"
    assert (
        hass.states.get("binary_sensor.smart_water_shutoff_pending_system_alerts").state
        == "on"
    )
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.smart_water_shutoff_shutoff_valve"},
        blocking=True,
    )
    assert hass.states.get("switch.smart_water_shutoff_shutoff_valve").state == "off"
    await async_setup_component(hass, HOMEASSISTANT_DOMAIN, {})
    with patch(
        "aioflo.water.Water.get_consumption_info",
        return_value={"aggregations": {"sumTotalGallonsConsumed": 0}},
    ) as water:
        await hass.services.async_call(
            HOMEASSISTANT_DOMAIN,
            SERVICE_UPDATE_ENTITY,
            {ATTR_ENTITY_ID: USAGE},
            blocking=True,
        )
        water.assert_awaited_once()
    assert hass.states.get(USAGE).state == "0"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_manual_refresh_reconnects_with_polling_disabled(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A manual usage refresh also refreshes cached device availability."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    info = await async_load_json_object_fixture(hass, "flo/device_info_response.json")
    info["isConnected"] = False
    detector_info = await async_load_json_object_fixture(
        hass, "flo/device_info_response_detector.json"
    )
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    with patch(
        "aioflo.device.Device.get_info",
        side_effect={"98765": info, "32839": detector_info}.__getitem__,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == STATE_UNAVAILABLE

    calls = aioclient_mock.call_count
    await async_refresh_entity(hass, USAGE)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert aioclient_mock.call_count == calls + 3
    assert hass.states.get(USAGE).state == "3.7"
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize(
    ("retry_day_change", "expected_state"),
    [pytest.param(0, "2", id="fresh"), pytest.param(1, STATE_UNAVAILABLE, id="stale")],
)
async def test_midnight_response(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    retry_day_change: int,
    expected_state: str,
) -> None:
    """Retry yesterday's response once and wait for it during a manual refresh."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    responses = iter(((1, 100), (retry_day_change, 2)))

    async def cross_midnight(*args: object, **kwargs: object) -> dict[str, object]:
        day_change, total = next(responses)
        freezer.tick(timedelta(days=day_change))
        return {"aggregations": {"sumTotalGallonsConsumed": total}}

    with patch(
        "aioflo.water.Water.get_consumption_info", side_effect=cross_midnight
    ) as water:
        await async_refresh_entity(hass, USAGE)
        assert hass.states.get(USAGE).state == expected_state
        await hass.async_block_till_done(wait_background_tasks=True)
        assert water.await_count == 2
        first, retry = water.await_args_list
        assert retry.args[1:3] == tuple(
            value + timedelta(days=1) for value in first.args[1:3]
        )
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize("error", [RequestError("offline"), TimeoutError()])
async def test_midnight_retry_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    """A failed rollover retry stays unavailable without scheduling more work."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    consumption = config_entry.runtime_data.devices[0]
    previous = consumption._water_usage

    async def cross_midnight(*args: object, **kwargs: object) -> dict[str, object]:
        freezer.tick(timedelta(days=1))
        water.side_effect = error
        return {"aggregations": {"sumTotalGallonsConsumed": 100}}

    with patch(
        "aioflo.water.Water.get_consumption_info", side_effect=cross_midnight
    ) as water:
        await async_refresh_entity(hass, USAGE)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert water.await_count == 2
    assert consumption._water_usage == previous
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize("payload", [{}, {"aggregations": {}}, {"aggregations": None}])
async def test_missing_total(
    hass: HomeAssistant, config_entry: MockConfigEntry, payload: dict[str, object]
) -> None:
    """Missing consumption must not be invented as zero."""
    config_entry.add_to_hass(hass)
    with patch("aioflo.water.Water.get_consumption_info", return_value=payload):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize(
    "consumption_status", [HTTPStatus.BAD_REQUEST, HTTPStatus.INTERNAL_SERVER_ERROR]
)
async def test_http_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """An actual library HTTP failure is isolated from live endpoints."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"
    assert (
        hass.states.get("binary_sensor.smart_water_shutoff_pending_system_alerts").state
        == "on"
    )
    assert hass.states.get("switch.smart_water_shutoff_shutoff_valve").state == "on"
    await hass.services.async_call(
        "switch",
        "turn_off",
        {ATTR_ENTITY_ID: "switch.smart_water_shutoff_shutoff_valve"},
        blocking=True,
    )
    assert hass.states.get("switch.smart_water_shutoff_shutoff_valve").state == "off"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_midnight_retry_shares_timeout(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The retry must finish within the original request's timeout budget."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    async def expire_retry(*args: object, **kwargs: object) -> dict[str, object]:
        clock.return_value = started_at + 21
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return {"aggregations": {"sumTotalGallonsConsumed": 2}}

    async def cross_midnight(*args: object, **kwargs: object) -> dict[str, object]:
        clock.return_value = started_at + 15
        freezer.tick(timedelta(days=1))
        water.side_effect = expire_retry
        return {"aggregations": {"sumTotalGallonsConsumed": 100}}

    with (
        patch.object(loop, "time", return_value=started_at) as clock,
        patch(
            "aioflo.water.Water.get_consumption_info", side_effect=cross_midnight
        ) as water,
    ):
        await async_refresh_entity(hass, USAGE)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert water.await_count == 2
        assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_slow_consumption_does_not_delay_telemetry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Live telemetry and alerts update while consumption is still waiting."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow_consumption(*args: object, **kwargs: object) -> dict[str, object]:
        started.set()
        await finish.wait()
        return {"aggregations": {"sumTotalGallonsConsumed": 10}}

    info = await async_load_json_object_fixture(hass, "flo/device_info_response.json")
    info["telemetry"]["current"]["gpm"] = 2
    info["notifications"]["pending"]["warningCount"] = 0
    with patch(
        "aioflo.water.Water.get_consumption_info", side_effect=slow_consumption
    ) as water:
        task = hass.async_create_background_task(
            async_refresh_entity(hass, USAGE), "test usage refresh"
        )
        await started.wait()
        try:
            # Let the live refresh's debounce cooldown expire.
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()
            with patch("aioflo.device.Device.get_info", return_value=info):
                await async_refresh_entity(hass, MODE)
            assert (
                hass.states.get("sensor.smart_water_shutoff_water_flow_rate").state
                == "2"
            )
            assert (
                hass.states.get(
                    "binary_sensor.smart_water_shutoff_pending_system_alerts"
                ).state
                == "off"
            )
            assert not task.done()
        finally:
            finish.set()
            await task
        water.assert_awaited_once()
    assert hass.states.get(USAGE).state == "10"
