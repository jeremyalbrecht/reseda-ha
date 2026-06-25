"""Test the Réséda config flow."""

from unittest.mock import AsyncMock

from custom_components.reseda.api import ResedaAuthError, ResedaConnectionError
from custom_components.reseda.const import CONF_PASC_ID, DOMAIN
import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import PASC_ONE, PASC_TWO

from .common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


async def test_single_pasc_flow(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Account with one PASC creates the entry directly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Réséda 10926157200007 (électricité)"
    assert result["data"][CONF_PASC_ID] == PASC_ONE.id
    assert result["data"][CONF_USERNAME] == "user@example.com"
    assert mock_setup_entry.call_count == 1


async def test_multi_pasc_flow(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_setup_entry: AsyncMock,
) -> None:
    """Account with several PASCs requires a selection step."""
    mock_client.async_get_pascs.return_value = [PASC_ONE, PASC_TWO]
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_pasc"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASC_ID: PASC_TWO.id},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PASC_ID] == PASC_TWO.id


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (ResedaAuthError("nope"), "invalid_auth"),
        (ResedaConnectionError("offline"), "cannot_connect"),
    ],
)
async def test_recovers_from_errors(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_setup_entry: AsyncMock,
    exc: Exception,
    expected_error: str,
) -> None:
    """Login errors surface, then recover on a retry."""
    mock_client.async_ensure_token.side_effect = exc
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}

    mock_client.async_ensure_token.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Adding the same PASC twice aborts."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "secret"},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_saves_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The single-field options flow stores the price."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"price_per_kwh": 0.2516}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"price_per_kwh": 0.2516}
