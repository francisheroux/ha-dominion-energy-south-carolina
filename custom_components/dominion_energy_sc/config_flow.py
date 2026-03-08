"""Config flow for Dominion Energy South Carolina."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.config_entries import OptionsFlow  # noqa: F401 (reserved for future)
from homeassistant.core import HomeAssistant

from .api import (
    CannotConnectError,
    DominionEnergySCClient,
    InvalidCredentialsError,
    OTPRequiredError,
)
from .const import CONF_COOKIES, CONF_PASSWORD, CONF_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class DominionEnergySCConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dominion Energy South Carolina."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._accounts: list[dict] = []
        self._send_methods: list[str] = []
        self._client: DominionEnergySCClient | None = None
        self._session: aiohttp.ClientSession | None = None
        self._is_reauth: bool = False

    async def _async_close_session(self) -> None:
        """Close the aiohttp session if open."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _async_create_client(self) -> DominionEnergySCClient:
        """Create a new aiohttp session and client, storing both as instance attrs."""
        await self._async_close_session()
        cookie_jar = aiohttp.CookieJar()
        self._session = aiohttp.ClientSession(cookie_jar=cookie_jar)
        self._client = DominionEnergySCClient(self._session)
        return self._client

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            client = await self._async_create_client()
            try:
                await client.async_login(username, password)
            except OTPRequiredError as exc:
                self._username = username
                self._password = password
                self._send_methods = exc.send_methods
                return await self.async_step_select_delivery()
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
                await self._async_close_session()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
                await self._async_close_session()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow login")
                errors["base"] = "unknown"
                await self._async_close_session()
            else:
                self._username = username
                self._password = password
                return await self._async_finish_login()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _async_finish_login(self) -> ConfigFlowResult:
        """Fetch account listing after successful auth and route to next step."""
        assert self._client is not None
        try:
            listing = await self._client.async_get_account_listing()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Failed to fetch account listing after login")
            await self._async_close_session()
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "unknown"},
            )

        # Validate the listing actually contains account data before proceeding
        has_account = listing.get("accountNumber") or listing.get("accountListing")
        if not has_account:
            _LOGGER.error("Account listing returned no accounts: %s", listing)
            await self._async_close_session()
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "no_accounts"},
            )

        await self._async_close_session()

        if self._is_reauth:
            reauth_entry = self._get_reauth_entry()
            return self.async_update_reload_and_abort(
                reauth_entry,
                data_updates={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_COOKIES: {},
                },
            )

        if listing.get("singleAccount", True):
            enc_num = listing.get("accountNumber", self._username)
            await self.async_set_unique_id(enc_num)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Dominion Energy SC ({listing.get('accountNumberFormatted', self._username)})",
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_COOKIES: {},
                },
            )

        accounts = listing.get("accountListing", [])
        if not accounts:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "no_accounts"},
            )

        self._accounts = accounts
        return await self.async_step_select_account()

    async def async_step_select_delivery(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick SMS/email delivery for MFA code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            send_method = user_input["send_method"]
            assert self._client is not None
            try:
                await self._client.async_send_pin(send_method)
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error sending PIN")
                errors["base"] = "unknown"
            else:
                return await self.async_step_enter_otp()

        method_options = {m: m for m in self._send_methods}
        return self.async_show_form(
            step_id="select_delivery",
            data_schema=vol.Schema(
                {vol.Required("send_method"): vol.In(method_options)}
            ),
            errors=errors,
        )

    async def async_step_enter_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt user for the 6-digit OTP code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            pin_code = user_input["otp_code"]
            assert self._client is not None
            try:
                await self._client.async_verify_pin(pin_code)
            except InvalidCredentialsError:
                errors["base"] = "invalid_otp"
            except CannotConnectError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error verifying PIN")
                errors["base"] = "unknown"
            else:
                return await self._async_finish_login()

        return self.async_show_form(
            step_id="enter_otp",
            data_schema=vol.Schema({vol.Required("otp_code"): str}),
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
        self._is_reauth = True
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

            client = await self._async_create_client()
            try:
                await client.async_login(username, password)
            except OTPRequiredError as exc:
                self._username = username
                self._password = password
                self._send_methods = exc.send_methods
                return await self.async_step_select_delivery()
            except InvalidCredentialsError:
                errors["base"] = "invalid_credentials"
                await self._async_close_session()
            except CannotConnectError:
                errors["base"] = "cannot_connect"
                await self._async_close_session()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
                await self._async_close_session()
            else:
                self._username = username
                self._password = password
                await self._async_close_session()
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
