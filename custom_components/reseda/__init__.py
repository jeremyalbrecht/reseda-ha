"""The Réséda integration."""

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ResedaClient
from .const import CONF_REFRESH_TOKEN
from .coordinator import ResedaConfigEntry, ResedaCoordinator

_PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ResedaConfigEntry) -> bool:
    """Set up Réséda from a config entry."""
    session = async_get_clientsession(hass)
    client = ResedaClient(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
    )
    coordinator = ResedaCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ResedaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: ResedaConfigEntry) -> None:
    """Re-import statistics so a new price applies immediately to new days."""
    await entry.runtime_data.async_request_refresh()
