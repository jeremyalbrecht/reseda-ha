"""Common fixtures for the Réséda tests."""

from collections.abc import Generator
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

from custom_components.reseda.api import Address, DailyReading, PascSummary
from custom_components.reseda.const import (
    CONF_PASC_ID,
    CONF_PASC_REF,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
import pytest

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .common import MockConfigEntry

PASC_ONE = PascSummary(
    id="pasc-id-one",
    reference="1096156",
    activity="électricité",
    activation_date=datetime(2025, 8, 29, tzinfo=UTC),
    pds_reference="10926157200007",
    pds_id="pds-id-one",
    delivery_space_id="edl-one",
)

PASC_TWO = PascSummary(
    id="pasc-id-two",
    reference="1096157",
    activity="électricité",
    activation_date=datetime(2024, 1, 15, tzinfo=UTC),
    pds_reference="10926157200007",
    pds_id="pds-id-two",
    delivery_space_id="edl-two",
)

ADDRESS_ONE = Address(
    line="13 Chem. des Fidanniers",
    commune="Poissy",
    code_postal="78300",
)

HISTORY_ONE = [
    DailyReading(
        day=date(2025, 8, 30),
        poste_mnemo="HPH",
        consumption_kwh=4.0,
        index_kwh=1758.0,
    ),
    DailyReading(
        day=date(2025, 8, 30),
        poste_mnemo="HCH",
        consumption_kwh=2.5,
        index_kwh=842.0,
    ),
    DailyReading(
        day=date(2025, 8, 31),
        poste_mnemo="HPH",
        consumption_kwh=5.0,
        index_kwh=1763.0,
    ),
]


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.reseda.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch ResedaClient everywhere it is used."""
    instance = AsyncMock()
    instance.refresh_token = "refresh-token-abc"
    instance.async_ensure_token = AsyncMock()
    instance.async_get_pascs = AsyncMock(return_value=[PASC_ONE])
    instance.async_get_address = AsyncMock(return_value=ADDRESS_ONE)
    instance.async_get_history = AsyncMock(return_value=HISTORY_ONE)

    with (
        patch("custom_components.reseda.ResedaClient", return_value=instance),
        patch(
            "custom_components.reseda.config_flow.ResedaClient", return_value=instance
        ),
    ):
        yield instance


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Build a config entry for the default PASC."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Réséda 10926157200007",
        unique_id=PASC_ONE.id,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_REFRESH_TOKEN: "refresh-token-abc",
            CONF_PASC_ID: PASC_ONE.id,
            CONF_PASC_REF: PASC_ONE.pds_reference,
        },
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> MockConfigEntry:
    """Add the config entry and complete setup.

    Tests using this fixture must also include ``recorder_mock`` and
    ``enable_custom_integrations`` (via ``@pytest.mark.usefixtures``) so that
    pytest orders fixture setup correctly.
    """
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
