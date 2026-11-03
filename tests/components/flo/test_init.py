"""Test init."""

from datetime import timedelta
import logging
from unittest.mock import AsyncMock, patch

from aioflo.api import SSO_TOKEN_URL
from aioflo.errors import RequestError
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_load_fixture,
)
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from syrupy.assertion import SnapshotAssertion
from yarl import URL

from homeassistant.components.flo.const import CONF_USE_SSO
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_setup_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test migration of config entry from v1."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    assert (
        dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
        == snapshot
    )

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_entry_persists_sso_flag(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Test setup stores use_sso when legacy auth fails and SSO succeeds."""
    config_entry.add_to_hass(hass)
    assert CONF_USE_SSO not in config_entry.data

    client = AsyncMock()
    client.user.get_info = AsyncMock(return_value={"locations": []})

    with patch(
        "homeassistant.components.flo.async_get_api",
        new_callable=AsyncMock,
        side_effect=[RequestError("legacy failed"), client],
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data[CONF_USE_SSO] is True


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 503])
async def test_discovery_failure_retries(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    status: int,
    request: pytest.FixtureRequest,
) -> None:
    """A failed discovery is retryable, without assuming credentials are invalid."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(config_entry, unique_id="existing-account")
    original_data = dict(config_entry.data)
    original_unique_id = config_entry.unique_id
    response = aioclient_mock.request(
        "get",
        "https://api-gw.meetflo.com/api/v2/users/12345abcde",
        status=status,
        text=await async_load_fixture(
            hass, "user_info_expand_locations_response.json", "flo"
        ),
    )
    request.getfixturevalue("aioclient_mock_fixture")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()
    assert config_entry.data == original_data
    assert config_entry.unique_id == original_unique_id

    response.status = 200
    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED
    assert (
        hass.states.get("sensor.smart_water_shutoff_current_system_mode").state
        == "home"
    )
    assert config_entry.data == original_data
    assert config_entry.unique_id == original_unique_id
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    calls_after_unload = aioclient_mock.call_count
    freezer.tick(timedelta(seconds=120))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == calls_after_unload


@pytest.mark.usefixtures("aioclient_mock_fixture")
async def test_discovery_does_not_log_user_data(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Discovery does not write the user's profile and locations to logs."""
    caplog.set_level(logging.DEBUG, logger="homeassistant.components.flo")
    config_entry.add_to_hass(hass)
    with patch(
        "aioflo.user.User.get_info",
        return_value={"locations": [], "email": "private-email-marker@example.com"},
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    assert "private-email-marker" not in caplog.text
    assert await hass.config_entries.async_unload(config_entry.entry_id)


async def test_discovery_recovers_rejected_token(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    request: pytest.FixtureRequest,
) -> None:
    """The library recovers a rejected token without a HA reauth flow."""
    rejected = AiohttpClientMockResponse(
        "get", URL("https://api-gw.meetflo.com/api/v2/users/12345abcde"), status=401
    )
    recovered = AiohttpClientMockResponse(
        "get",
        URL("https://api-gw.meetflo.com/api/v2/users/12345abcde"),
        text=await async_load_fixture(
            hass, "user_info_expand_locations_response.json", "flo"
        ),
    )
    aioclient_mock.get(
        "https://api-gw.meetflo.com/api/v2/users/12345abcde",
        side_effect=AsyncMock(side_effect=[rejected, recovered]),
    )
    request.getfixturevalue("aioclient_mock_fixture")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()
    assert (
        hass.states.get("sensor.smart_water_shutoff_current_system_mode").state
        == "home"
    )
    assert (
        len(
            [
                call
                for call in aioclient_mock.mock_calls
                if call[1].path == "/api/v1/users/auth"
            ]
        )
        == 2
    )
    assert await hass.config_entries.async_unload(config_entry.entry_id)


@pytest.mark.parametrize(
    ("use_sso", "expected_paths"),
    [
        (False, ["/api/v1/users/auth", "/prod/v1/oauth2/token"]),
        (True, ["/prod/v1/oauth2/token"]),
    ],
)
@pytest.mark.parametrize("status", [400, 401, 403, 429, 503])
async def test_auth_failure_is_retryable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    use_sso: bool,
    expected_paths: list[str],
    status: int,
) -> None:
    """Ambiguous login failures must not demand new credentials or change mode."""
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry,
        data={**config_entry.data, CONF_USE_SSO: use_sso},
        unique_id="existing-account",
    )
    original_data = dict(config_entry.data)
    aioclient_mock.post("https://api.meetflo.com/api/v1/users/auth", status=status)
    aioclient_mock.post(SSO_TOKEN_URL, status=status)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert config_entry.data == original_data
    assert config_entry.unique_id == "existing-account"
    assert not hass.config_entries.flow.async_progress()
    assert [call[1].path for call in aioclient_mock.mock_calls] == expected_paths
