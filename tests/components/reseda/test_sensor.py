"""Sensor tests for the Réséda integration."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.reseda.const import CONF_PRICE_ENTITY, CONF_PRICE_PER_KWH
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant, State

from .common import MockConfigEntry

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


COST_ID = "reseda:10926157200007_cost_total"
PRICE_ENTITY = "sensor.electricity_price"


async def _cost_stats(hass: HomeAssistant) -> list[tuple[float, float]]:
    """Return (state, sum) of the imported cost statistic, oldest first."""
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2025, 1, 1, tzinfo=UTC),
        None,
        {COST_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    return [
        (round(row["state"], 4), round(row["sum"], 4)) for row in stats.get(COST_ID, [])
    ]


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, options: dict) -> None:
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# Fixture history: 6.5 kWh on 2025-08-30, 5.0 kWh on 2025-08-31.


async def test_cost_fixed_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without a price entity the fixed price is applied to every day."""
    await _setup(hass, mock_config_entry, {CONF_PRICE_PER_KWH: 0.2})
    assert await _cost_stats(hass) == [(1.3, 1.3), (1.0, 2.3)]


async def test_cost_entity_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The entity price applies from its valid_from date."""
    hass.states.async_set(PRICE_ENTITY, "0.3", {"valid_from": "2025-08-01"})
    await _setup(
        hass,
        mock_config_entry,
        {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
    )
    assert await _cost_stats(hass) == [(1.95, 1.95), (1.5, 3.45)]


async def test_cost_entity_without_fixed_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """An entity alone is enough; its earliest value covers older days."""
    hass.states.async_set(PRICE_ENTITY, "0.3")
    await _setup(hass, mock_config_entry, {CONF_PRICE_ENTITY: PRICE_ENTITY})
    assert await _cost_stats(hass) == [(1.95, 1.95), (1.5, 3.45)]


async def test_cost_days_before_entity_history_use_fixed_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Days older than any known entity value keep the fixed price."""
    hass.states.async_set(PRICE_ENTITY, "0.3")  # no valid_from: applies from now
    await _setup(
        hass,
        mock_config_entry,
        {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
    )
    assert await _cost_stats(hass) == [(1.3, 1.3), (1.0, 2.3)]


async def test_cost_historical_price_per_day(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Each day uses the price that was in force on that day."""
    old = State(
        PRICE_ENTITY,
        "0.1",
        {"valid_from": "2025-02-01"},
        last_changed=datetime(2025, 2, 1, 6, tzinfo=UTC),
    )
    new = State(
        PRICE_ENTITY,
        "0.3",
        {"valid_from": "2025-08-31"},
        last_changed=datetime(2025, 8, 31, 9, tzinfo=UTC),
    )
    with patch(
        "custom_components.reseda.price.history.state_changes_during_period",
        return_value={PRICE_ENTITY: [old, new]},
    ):
        await _setup(
            hass,
            mock_config_entry,
            {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
        )
    assert await _cost_stats(hass) == [(0.65, 0.65), (1.5, 2.15)]


async def test_cost_historical_price_from_statistics(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Long-term statistics provide prices older than the state history."""
    day_30 = datetime(2025, 8, 29, 22, tzinfo=UTC)  # 2025-08-30 00:00 Paris
    rows = [
        {"start": (day_30 - timedelta(hours=1)).timestamp(), "mean": 0.1},
        {"start": (day_30 + timedelta(hours=10)).timestamp(), "mean": 0.3},
    ]
    with patch(
        "custom_components.reseda.price.statistics.statistics_during_period",
        return_value={PRICE_ENTITY: rows},
    ):
        await _setup(
            hass,
            mock_config_entry,
            {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
        )
    # The entity has no current state: the last recorded price (0.3) still
    # applies to the 31st, and a fallback warning is logged.
    assert await _cost_stats(hass) == [(0.65, 0.65), (1.5, 2.15)]


async def test_cost_unavailable_entity_falls_back_and_warns_once(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable entity with no history falls back to the fixed price."""
    hass.states.async_set(PRICE_ENTITY, "unavailable")
    await _setup(
        hass,
        mock_config_entry,
        {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
    )
    assert await _cost_stats(hass) == [(1.3, 1.3), (1.0, 2.3)]

    coordinator = mock_config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert caplog.text.count("using the fixed price 0.2") == 1

    # Recovering re-arms the warning.
    hass.states.async_set(PRICE_ENTITY, "0.3")
    await coordinator.async_refresh()
    hass.states.async_set(PRICE_ENTITY, "abc")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "is unavailable or not numeric; using its last recorded price" in (
        caplog.text
    )


async def test_cost_skipped_without_any_price(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No fixed price and an unusable entity: no cost statistic."""
    await _setup(hass, mock_config_entry, {CONF_PRICE_ENTITY: PRICE_ENTITY})
    assert await _cost_stats(hass) == []


async def test_cost_price_history_survives_recorder_purge(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Prices seen once keep applying after their states are purged."""
    hass.states.async_set(PRICE_ENTITY, "0.3", {"valid_from": "2025-08-01"})
    await _setup(
        hass,
        mock_config_entry,
        {CONF_PRICE_PER_KWH: 0.2, CONF_PRICE_ENTITY: PRICE_ENTITY},
    )
    assert await _cost_stats(hass) == [(1.95, 1.95), (1.5, 3.45)]

    hass.states.async_remove(PRICE_ENTITY)
    with patch(
        "custom_components.reseda.price.history.state_changes_during_period",
        return_value={},
    ):
        await mock_config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert await _cost_stats(hass) == [(1.95, 1.95), (1.5, 3.45)]

    assert await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()
