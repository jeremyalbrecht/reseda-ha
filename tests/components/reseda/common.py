"""Minimal test helpers for the Réséda integration tests."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant


class MockConfigEntry(config_entries.ConfigEntry):
    """Minimal MockConfigEntry that avoids the heavy tests.common dependency."""

    def __init__(
        self,
        *,
        data=None,
        disabled_by=None,
        discovery_keys=None,
        domain="test",
        entry_id=None,
        minor_version=1,
        options=None,
        pref_disable_new_entities=None,
        pref_disable_polling=None,
        source=config_entries.SOURCE_USER,
        state=None,
        subentries_data=None,
        title="Mock Title",
        unique_id=None,
        version=1,
    ) -> None:
        kwargs = {
            "data": data or {},
            "disabled_by": disabled_by,
            "discovery_keys": discovery_keys or {},
            "domain": domain,
            "entry_id": entry_id,
            "minor_version": minor_version,
            "options": options or {},
            "pref_disable_new_entities": pref_disable_new_entities,
            "pref_disable_polling": pref_disable_polling,
            "subentries_data": subentries_data or (),
            "title": title,
            "unique_id": unique_id,
            "version": version,
        }
        if source is not None:
            kwargs["source"] = source
        if state is not None:
            kwargs["state"] = state
        super().__init__(**kwargs)

    def add_to_hass(self, hass: HomeAssistant) -> None:
        """Test helper to add this entry to hass."""
        hass.config_entries._entries[self.entry_id] = self
