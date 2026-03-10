"""Dominion Energy South Carolina API client."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
    ENDPOINT_ACCESS,
    ENDPOINT_ACCOUNT_INIT,
    ENDPOINT_AUTH,
    ENDPOINT_DAILY,
    ENDPOINT_ENERGY,
    ENDPOINT_GET_AFT,
    ENDPOINT_INIT_AUTH,
    ENDPOINT_LISTING,
    ENDPOINT_PAYMENT,
    ENDPOINT_SELECT,
    ENDPOINT_SEND_PIN,
    ENDPOINT_USAGE,
    ENDPOINT_VERIFY_2FA_TOKEN,
    ENDPOINT_VERIFY_PIN,
)

_LOGGER = logging.getLogger(__name__)

class CannotConnectError(Exception):
    """Cannot connect to Dominion Energy SC portal."""

class InvalidCredentialsError(Exception):
    """Invalid username or password."""

class SessionExpiredError(Exception):
    """Session has expired."""

class ApiError(Exception):
    """Non-zero returnCode from the API."""

class OTPRequiredError(Exception):
    """MFA required."""
    def __init__(self, send_methods: list[str]) -> None:
        self.send_methods = send_methods
        super().__init__(f"OTP required; methods: {send_methods}")

class DominionEnergySCClient:
    """Async HTTP client for the Dominion Energy SC customer portal."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._api_csrf_token: str | None = None

    @property
    def _api_headers(self) -> dict[str, str]:
        """Return standardized headers. Note: No () when calling this."""
        return {
            "__RequestVerificationToken": self._api_csrf_token or "",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "isajax": "true",
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        }

    async def async_setup_session(self) -> None:
        """Establish session and extract CSRF."""
        try:
            async with self._session.get(BASE_URL + ENDPOINT_ACCESS) as resp:
                html = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
        if match:
            self._api_csrf_token = match.group(1)
        else:
            # Fallback to cookies
            for cookie in self._session.cookie_jar:
                if cookie.key == "__RequestVerificationToken":
                    self._api_csrf_token = cookie.value
                    break

    async def async_login(self, username: str, password: str) -> None:
        """Main login entry point."""
        await self.async_setup_session()

        payload = await self._post_json(
            ENDPOINT_AUTH,
            {"userName": username, "password": password, "_df": ""}
        )

        # Handle MFA Logic
        mfa_required = (
            payload.get("requiresMFA")
            or str(payload.get("returnCode")) in ("2", "3", "10")
            or payload.get("data", {}).get("status") == "twoFA"
        )

        if mfa_required:
            await self._async_refresh_csrf_from_aft()
            await self._async_check_device_token()
            send_methods = await self._async_get_send_methods()
            raise OTPRequiredError(send_methods)

        if str(payload.get("returnCode", "0")) not in ("0", "1"):
            raise InvalidCredentialsError(payload.get("pageMessage", "Login failed"))

        await self.async_get_aft()

    async def async_send_pin(self, send_method: str) -> None:
        """Send the MFA PIN."""
        await self._post_json(ENDPOINT_SEND_PIN, {"sendMethod": send_method, "_df": ""})

    async def async_verify_pin(self, pin_code: str) -> None:
        """Verify the MFA PIN."""
        payload = await self._post_json(
            ENDPOINT_VERIFY_PIN,
            {"PINcode": pin_code, "registerDevice": True, "_df": ""}
        )
        if str(payload.get("returnCode", "0")) not in ("0", "1"):
            raise InvalidCredentialsError("Invalid OTP code")
        await self.async_get_aft()

    async def _post_json(self, endpoint: str, body: dict) -> Any:
        """Helper for POST requests with error handling."""
        try:
            async with self._session.post(
                BASE_URL + endpoint,
                headers=self._api_headers,  # NO PARENTHESES HERE
                json=body
            ) as resp:
                self._check_session_expired(resp)
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

    async def _get_json(self, endpoint: str, params: dict | None = None) -> Any:
        """Helper for GET requests."""
        try:
            async with self._session.get(
                BASE_URL + endpoint,
                headers=self._api_headers, # NO PARENTHESES HERE
                params=params
            ) as resp:
                self._check_session_expired(resp)
                return await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

    def _check_session_expired(self, resp: aiohttp.ClientResponse) -> None:
        if resp.status in (401, 403, 555):
            raise SessionExpiredError("Session expired")
        if "json" not in (resp.content_type or "").lower():
            raise SessionExpiredError("Non-JSON response (WAF block or redirect)")
        content_type = resp.content_type or ""
        if "json" not in content_type.lower():
            # If the server sends HTML instead of JSON, we've likely been redirected to a login page
            _LOGGER.warning("Non-JSON response received from %s", resp.url)
            raise SessionExpiredError("Received non-JSON response; session likely expired or blocked.")

    async def _get_json(self, endpoint: str, params: dict | None = None) -> Any:
        """GET an API endpoint and return parsed JSON data field."""
        try:
            async with self._session.get(
                BASE_URL + endpoint,
                headers=self._api_headers,
                params=params,
                allow_redirects=True,
            ) as resp:
                self._check_session_expired(resp)
                payload = await resp.json(content_type=None)
        except SessionExpiredError:
            raise
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        return_code = str(payload.get("returnCode", "0"))
        if return_code != "0":
            raise ApiError(
                f"API {endpoint} returned code {return_code}: "
                f"{payload.get('pageMessage')}"
            )
        return payload.get("data", payload)

    async def _post_json(self, endpoint: str, body: dict) -> Any:
        """POST JSON to an API endpoint and return parsed JSON."""
        try:
            async with self._session.post(
                BASE_URL + endpoint,
                headers=self._api_headers,
                json=body,
                allow_redirects=True,
            ) as resp:
                self._check_session_expired(resp)
                payload = await resp.json(content_type=None)
        except SessionExpiredError:
            raise
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        return_code = str(payload.get("returnCode", "0"))
        if return_code != "0":
            raise ApiError(
                f"API {endpoint} returned code {return_code}: "
                f"{payload.get('pageMessage')}"
            )
        return payload.get("data", payload)

    async def async_get_account_listing(self) -> dict:
        """Return account listing (single or multi-account)."""
        return await self._get_json(ENDPOINT_LISTING)

    async def async_select_account(self, encrypted_number: str) -> bool:
        """Select the active account for subsequent API calls."""
        result = await self._post_json(
            ENDPOINT_SELECT,
            {"EncryptedAccountNumber": encrypted_number, "_df": ""},
        )
        return bool(result)

    async def async_get_account_summary(self) -> dict:
        """Return account summary: balance, due date, last payment."""
        return await self._get_json(ENDPOINT_ACCOUNT_INIT)

    async def async_get_payment_widget(self) -> dict:
        """Return numeric balance and due date from payment widget."""
        return await self._get_json(ENDPOINT_PAYMENT)

    async def async_get_energy_analyzer(self) -> dict:
        """Return monthly usage/cost per utility (primary usage endpoint)."""
        return await self._get_json(ENDPOINT_ENERGY)

    async def async_get_all_usage_data(self) -> dict:
        """Return per-bill line items, gas/electric split."""
        return await self._get_json(ENDPOINT_USAGE)

    async def async_get_daily_usage(
        self,
        start: str = "1900-01-01",
        end: str = "1900-01-01",
        revenue_month: int = 0,
    ) -> dict:
        """Return daily kWh/CCF arrays for a billing period.

        Use start='1900-01-01' and revenue_month=0 for the current period.
        """
        params = {
            "startDate": start,
            "endDate": end,
            "electricStartDate": start,
            "electricEndDate": end,
            "gasStartDate": start,
            "gasEndDate": end,
            "revenueMonth": revenue_month,
            "callType": "L",
        }
        return await self._get_json(ENDPOINT_DAILY, params=params)
