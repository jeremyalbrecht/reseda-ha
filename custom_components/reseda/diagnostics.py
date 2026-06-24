"""Diagnostics support for the Réséda integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_REFRESH_TOKEN
from .coordinator import ResedaConfigEntry

_REDACTED = {CONF_PASSWORD, CONF_USERNAME, CONF_REFRESH_TOKEN}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ResedaConfigEntry
) -> dict[str, Any]:
    """Return diagnostic info."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), _REDACTED),
        "data": _serialise(coordinator.data) if coordinator.data else None,
        "last_update_success": coordinator.last_update_success,
    }


def _serialise(data: Any) -> dict[str, Any]:
    raw = asdict(data)
    pasc = raw.get("pasc")
    if isinstance(pasc, dict) and pasc.get("activation_date") is not None:
        pasc["activation_date"] = str(pasc["activation_date"])
    last = raw.get("last_reading_date")
    if last is not None:
        raw["last_reading_date"] = str(last)
    return raw
