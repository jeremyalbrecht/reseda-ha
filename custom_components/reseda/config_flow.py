"""Config flow for the Réséda integration."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    PascSummary,
    ResedaApiError,
    ResedaAuthError,
    ResedaClient,
    ResedaConnectionError,
)
from .const import (
    CONF_PASC_ID,
    CONF_PASC_REF,
    CONF_PRICE_PER_KWH,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ResedaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Réséda."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._refresh_token: str | None = None
        self._pascs: list[PascSummary] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ResedaOptionsFlow:
        """Return the options flow handler."""
        return ResedaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect credentials and authenticate."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = ResedaClient(
                async_get_clientsession(self.hass),
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )
            try:
                await client.async_ensure_token()
                self._pascs = await client.async_get_pascs()
            except ResedaAuthError:
                errors["base"] = "invalid_auth"
            except ResedaConnectionError:
                errors["base"] = "cannot_connect"
            except ResedaApiError as err:
                _LOGGER.warning("Unexpected API error during login: %s", err)
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected exception during login")
                errors["base"] = "unknown"
            else:
                if not self._pascs:
                    errors["base"] = "no_pascs"
                else:
                    self._username = user_input[CONF_USERNAME]
                    self._password = user_input[CONF_PASSWORD]
                    self._refresh_token = client.refresh_token
                    if len(self._pascs) == 1:
                        return await self._async_create_entry(self._pascs[0])
                    return await self.async_step_select_pasc()

        return self.async_show_form(
            step_id="user",
            data_schema=_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_select_pasc(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the user's contracts."""
        if user_input is not None:
            pasc = next(p for p in self._pascs if p.id == user_input[CONF_PASC_ID])
            return await self._async_create_entry(pasc)

        schema = vol.Schema(
            {
                vol.Required(CONF_PASC_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {
                                "value": pasc.id,
                                "label": _label_for(pasc),
                            }
                            for pasc in self._pascs
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="select_pasc", data_schema=schema)

    async def _async_create_entry(self, pasc: PascSummary) -> ConfigFlowResult:
        await self.async_set_unique_id(pasc.id)
        self._abort_if_unique_id_configured()
        assert self._username is not None
        assert self._password is not None
        return self.async_create_entry(
            title=_label_for(pasc),
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_REFRESH_TOKEN: self._refresh_token,
                CONF_PASC_ID: pasc.id,
                CONF_PASC_REF: pasc.pds_reference or pasc.reference,
            },
        )


def _label_for(pasc: PascSummary) -> str:
    label = pasc.pds_reference or pasc.reference or pasc.id
    if pasc.activity:
        return f"Réséda {label} ({pasc.activity})"
    return f"Réséda {label}"


class ResedaOptionsFlow(OptionsFlow):
    """Single-field options flow — just the €/kWh price."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show / save the price per kWh."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_PRICE_PER_KWH, 0.20)
        schema = vol.Schema(
            {
                vol.Required(CONF_PRICE_PER_KWH, default=current): NumberSelector(
                    NumberSelectorConfig(
                        min=0.001,
                        max=10.0,
                        step="any",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
