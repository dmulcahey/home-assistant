"""Test optional consumption startup and local-day boundaries."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .test_consumption import MODE, USAGE, async_refresh_entity


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_startup_does_not_wait_for_consumption(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
) -> None:
    """Healthy live entities load while the optional endpoint is blocked."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(*args: object, **kwargs: object) -> dict[str, object]:
        started.set()
        await release.wait()
        return {"aggregations": {"sumTotalGallonsConsumed": 2}}

    config_entry.add_to_hass(hass)
    with patch("aioflo.water.Water.get_consumption_info", side_effect=delayed):
        setup_task = hass.async_create_task(
            hass.config_entries.async_setup(config_entry.entry_id)
        )
        await started.wait()
        try:
            assert await asyncio.wait_for(asyncio.shield(setup_task), 1)
            assert hass.states.get(MODE).state == "home"
            assert (
                hass.states.get(
                    "binary_sensor.smart_water_shutoff_pending_system_alerts"
                ).state
                == "on"
            )
            assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
        finally:
            release.set()
            await setup_task
            await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_midnight_publishes_unavailable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Invalidate yesterday's state at midnight before the next polling tick."""
    await hass.config.async_update(time_zone="UTC")
    freezer.move_to("2026-02-01 23:59:50+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(USAGE).state != STATE_UNAVAILABLE
    with patch("aioflo.water.Water.get_consumption_info", side_effect=TimeoutError):
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
@pytest.mark.parametrize(
    ("start", "day_hours"),
    [("2026-03-08 05:00:00+00:00", 23), ("2026-11-01 04:00:00+00:00", 25)],
)
async def test_dst_midnight(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    start: str,
    day_hours: int,
) -> None:
    """The next local midnight follows a 23- or 25-hour DST day."""
    await hass.config.async_update(time_zone="America/New_York")
    freezer.move_to(start)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    previous = hass.states.get(USAGE).state
    with patch(
        "aioflo.water.Water.get_consumption_info", side_effect=TimeoutError
    ) as water:
        freezer.tick(timedelta(hours=day_hours, seconds=-1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert hass.states.get(USAGE).state == previous
        water.assert_not_called()
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_awaited_once()
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_unload_cancels_pending_consumption(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Unload cancels optional startup and removes midnight/timezone work."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def delayed(*args: object, **kwargs: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    config_entry.add_to_hass(hass)
    with patch("aioflo.water.Water.get_consumption_info", side_effect=delayed) as water:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await started.wait()
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert cancelled.is_set()
        freezer.tick(timedelta(days=2))
        async_fire_time_changed(hass)
        await hass.config.async_update(time_zone="America/New_York")
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_awaited_once()
    freezer.tick(timedelta(days=-2))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(USAGE).state != STATE_UNAVAILABLE
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_disabled_consumption_does_not_start_work(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A disabled consumption entity has no requests or day/timezone listeners."""
    config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        "sensor",
        "flo",
        "111111111111_daily_consumption",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
        suggested_object_id="smart_water_shutoff_today_s_water_usage",
    )
    with patch("aioflo.water.Water.get_consumption_info") as water:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        freezer.tick(timedelta(minutes=2))
        async_fire_time_changed(hass)
        await hass.config.async_update(time_zone="America/New_York")
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_not_called()
    assert hass.states.get(USAGE) is None
    assert hass.states.get(MODE).state == "home"


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_concurrent_refresh_requires_auth_recovery(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Live and consumption requests must wait for shared token renewal."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    client = config_entry.runtime_data.client
    client._token_expiration = dt_util.utcnow().replace(tzinfo=None) - timedelta(
        seconds=1
    )
    authenticate = client._async_authenticate_legacy
    started = asyncio.Event()
    release = asyncio.Event()
    live_started = asyncio.Event()
    ping = client.presence.ping

    async def live_ping() -> None:
        live_started.set()
        await ping()

    async def delayed_authenticate() -> None:
        started.set()
        await release.wait()
        await authenticate()

    with (
        patch.object(
            client, "_async_authenticate_legacy", side_effect=delayed_authenticate
        ),
        patch.object(client.presence, "ping", side_effect=live_ping),
    ):
        usage_task = hass.async_create_background_task(
            async_refresh_entity(hass, USAGE), "test usage refresh"
        )
        await started.wait()
        live_task = hass.async_create_background_task(
            async_refresh_entity(hass, MODE), "test live refresh"
        )
        try:
            await live_started.wait()
        finally:
            release.set()
            await asyncio.gather(usage_task, live_task)
    protected_headers = [
        headers
        for _, url, _, headers in aioclient_mock.mock_calls
        if url.host == "api-gw.meetflo.com"
    ]
    assert all(headers.get("Authorization") for headers in protected_headers)
    assert hass.states.get(MODE).state == "home"
    assert hass.states.get(USAGE).state != STATE_UNAVAILABLE


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_timezone_reschedules_midnight(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Changing zone removes the old midnight timer and uses the new local day."""
    await hass.config.async_update(time_zone="UTC")
    freezer.move_to("2026-02-01 23:59:50+00:00")
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, pref_disable_polling=True)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    with patch(
        "aioflo.water.Water.get_consumption_info",
        side_effect=[
            {"aggregations": {"sumTotalGallonsConsumed": 2}},
            TimeoutError,
        ],
    ) as water:
        await hass.config.async_update(time_zone="America/New_York")
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_awaited_once()
        previous = hass.states.get(USAGE).state
        freezer.tick(timedelta(seconds=10))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        water.assert_awaited_once()
        assert hass.states.get(USAGE).state == previous
        freezer.tick(timedelta(hours=5))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert water.await_count == 2
    assert hass.states.get(USAGE).state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_disabling_sensor_cancels_pending_consumption(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Removing the consumption listener cancels its outstanding request."""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def delayed(*args: object, **kwargs: object) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    config_entry.add_to_hass(hass)
    with patch("aioflo.water.Water.get_consumption_info", side_effect=delayed):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await started.wait()
        entity_registry.async_update_entity(
            USAGE, disabled_by=er.RegistryEntryDisabler.USER
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert cancelled.is_set()
    assert hass.states.get(USAGE) is None
    assert hass.states.get(MODE).state == "home"
