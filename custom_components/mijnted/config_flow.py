"""Config flow for mijnTED."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    MijntedAuthError,
    MijntedClient,
    MijntedConnectionError,
    MijntedUnexpectedPageError,
    create_cookie_jar,
)
from .const import (
    CONF_PRICE_PER_UNIT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_UPDATE_INTERVAL_HOURS,
    MIN_UPDATE_INTERVAL_HOURS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .coordinator import MijntedConfigEntry

_LOGGER = logging.getLogger(__name__)

EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


async def validate_credentials(hass: HomeAssistant, email: str, password: str) -> str:
    """Sign in once, check for residential units and return the account id."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=create_cookie_jar()
    )
    try:
        client = MijntedClient(
            session, email, password, dt_util.get_default_time_zone()
        )
        account_id = await client.async_sign_in()
        if not await client.async_get_residential_unit_ids():
            raise NoResidentialUnitsError
        return account_id
    finally:
        session.detach()


class NoResidentialUnitsError(Exception):
    """The account has no residential units (for example a landlord account)."""


async def _errors_for(
    hass: HomeAssistant, email: str, password: str
) -> tuple[str | None, dict[str, str]]:
    """Validate credentials and map failures to form errors.

    Error messages hold only a step name, HTTP status or exception type, so they
    are logged at debug level to explain the form error.
    """
    try:
        return await validate_credentials(hass, email, password), {}
    except MijntedAuthError as err:
        _LOGGER.debug("Sign-in check: credentials rejected (%s)", err)
        return None, {"base": "invalid_auth"}
    except MijntedUnexpectedPageError as err:
        _LOGGER.debug("Sign-in check: %s", err)
        return None, {"base": "unexpected_page"}
    except MijntedConnectionError as err:
        _LOGGER.debug("Sign-in check: cannot connect (%s)", err)
        return None, {"base": "cannot_connect"}
    except NoResidentialUnitsError:
        return None, {"base": "no_residential_units"}
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Unexpected error while validating mijnTED credentials")
        return None, {"base": "unknown"}


class MijntedConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for mijnTED."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for e-mail and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            account_id, errors = await _errors_for(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_EMAIL): EMAIL_SELECTOR,
                        probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    }
                ),
                {CONF_EMAIL: user_input[CONF_EMAIL]} if user_input else {},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        _entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Start reauthentication after the portal rejected the stored credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account_id, errors = await _errors_for(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR}
            ),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the e-mail address or password of the same mijnTED account."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            account_id, errors = await _errors_for(
                self.hass, email, user_input[CONF_PASSWORD]
            )
            if account_id:
                await self.async_set_unique_id(account_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    title=email,
                    data_updates={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_EMAIL): EMAIL_SELECTOR,
                        probatio.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    }
                ),
                {
                    CONF_EMAIL: user_input[CONF_EMAIL]
                    if user_input
                    else entry.data[CONF_EMAIL]
                },
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: MijntedConfigEntry,  # noqa: ARG004 - Home Assistant's signature
    ) -> MijntedOptionsFlow:
        """Return the options flow."""
        return MijntedOptionsFlow()


class MijntedOptionsFlow(OptionsFlowWithReload):
    """Options: update interval and the price per unit for the cost estimate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the update interval in hours and the price per unit."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                    CONF_PRICE_PER_UNIT: float(
                        user_input.get(CONF_PRICE_PER_UNIT) or 0
                    ),
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_UPDATE_INTERVAL): NumberSelector(
                            NumberSelectorConfig(
                                min=MIN_UPDATE_INTERVAL_HOURS,
                                max=MAX_UPDATE_INTERVAL_HOURS,
                                step=1,
                                mode=NumberSelectorMode.BOX,
                                unit_of_measurement="h",
                            )
                        ),
                        probatio.Optional(CONF_PRICE_PER_UNIT): NumberSelector(
                            NumberSelectorConfig(
                                min=0,
                                step="any",
                                mode=NumberSelectorMode.BOX,
                                unit_of_measurement=self.hass.config.currency,
                            )
                        ),
                    }
                ),
                {
                    CONF_UPDATE_INTERVAL: self.config_entry.options.get(
                        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS
                    ),
                    CONF_PRICE_PER_UNIT: self.config_entry.options.get(
                        CONF_PRICE_PER_UNIT, 0
                    ),
                },
            ),
        )
