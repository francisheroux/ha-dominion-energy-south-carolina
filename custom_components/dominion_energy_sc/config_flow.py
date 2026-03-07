"""Config flow for Dominion Energy South Carolina."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.config_entries import OptionsFlow  # noqa: F401 (reserved for future)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .api import (
    CannotConnectError,
    DominionEnergySCClient,
    InvalidCredentialsError,
)
from .const import CONF_COOKIES, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _validate_and_discover(
    hass: HomeAssistant, username: str, password: str
) -> tuple[DominionEnergySCClient, aiohttp.CookieJar, dict]:
    """Attempt login and return (client, cookie_jar, listing_payload)."""
    cookie_jar = aiohttp.CookieJar()
    session = aiohttp.ClientSession(cookie_jar=cookie_jar)
    client = DominionEnergySCClient(session)
    try:
        await client.async_login(username, password)
        listing = await client.async_get_account_listing()
    except Exception:
        await session.close()
        raise
    # Don't close session — caller may need the cookies
    hass.async_create_task(session.close())
    return client, cookie_jar, listing


class DominionEnergySCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dominion Energy South Carolina."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._accounts: list[dict] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            try:
                _client, _jar, listing = await _validate_and_discover(
                    self.hass, username, password
                )
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                self._username = username
                self._password = password

                if listing.get("singleAccount", True):
                    # Single account — create entry immediately
                    enc_num = listing.get("accountNumber", username)
                    await self.async_set_unique_id(enc_num)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Dominion Energy SC ({listing.get('accountNumberFormatted', username)})",
                        data={
                            CONF_USERNAME: username,
                            CONF_PASSWORD: password,
                            CONF_COOKIES: {},
                        },
                    )

                # Multiple accounts — let user pick
                accounts = listing.get("accountListing", [])
                if not accounts:
                    errors["base"] = "no_accounts"
                else:
                    self._accounts = accounts
                    return await self.async_step_select_account()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_select_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which account to add (multi-account case)."""
        if user_input is not None:
            enc_num = user_input["account"]
            chosen = next(
                (a for a in self._accounts if a["accountNumber"] == enc_num), None
            )
            title = enc_num
            if chosen:
                title = (
                    f"Dominion Energy SC ({chosen.get('accountNumberFormatted', '')} "
                    f"- {chosen.get('accountTown', '')})"
                )
            await self.async_set_unique_id(enc_num)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=title,
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_COOKIES: {},
                },
            )

        account_options = {
            a["accountNumber"]: (
                f"{a.get('accountNumberFormatted', '???')} — "
                f"{a.get('accountTown', '')} "
                f"({'Closed' if a.get('closedAccount') else 'Active'})"
            )
            for a in self._accounts
        }

        return self.async_show_form(
            step_id="select_account",
            data_schema=vol.Schema(
                {vol.Required("account"): vol.In(account_options)}
            ),
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle re-auth confirmation step."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                await _validate_and_discover(self.hass, username, password)
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_COOKIES: {},
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )
