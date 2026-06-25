"""Setup/unload tests for the Réséda integration."""

from unittest.mock import AsyncMock

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .common import MockConfigEntry

pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    setup_integration: MockConfigEntry,
) -> None:
    """Setting up the entry stores a coordinator and we can unload it."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
