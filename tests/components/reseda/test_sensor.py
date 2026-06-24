"""Sensor tests for the Réséda integration."""

from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant

from tests.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def test_sensors(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    setup_integration: MockConfigEntry,
) -> None:
    """The sensors expose the cumulative kWh total + diagnostic data."""
    state = hass.states.get("sensor.reseda_10926157200007_total_consumption")
    assert state is not None
    # Fixture history: latest HPH index = 1763.0, latest HCH index = 842.0.
    assert float(state.state) == 1763.0 + 842.0

    prm = hass.states.get("sensor.reseda_10926157200007_prm")
    assert prm is not None
    assert prm.state == "10926157200007"

    address = hass.states.get("sensor.reseda_10926157200007_address")
    assert address is not None
    assert "Poissy" in address.state
