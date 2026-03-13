"""Dominion Energy South Carolina API client."""
from __future__ import annotations

import logging
import re
from typing import Any

import asyncio
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
    def __init__(self, send_methods: list[dict]) -> None:
        self.send_methods = send_methods
        super().__init__(f"OTP required; methods: {send_methods}")

class DominionEnergySCClient:
    """Async HTTP client for the Dominion Energy SC customer portal."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._api_csrf_token: str | None = None

    @property
    def _api_headers(self) -> dict[str, str]:
        """Return standardized headers."""
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
            for cookie in self._session.cookie_jar:
                if cookie.key == "__RequestVerificationToken":
                    self._api_csrf_token = cookie.value
                    break

    async def async_login(self, username: str, password: str) -> None:
        """Main login entry point."""
        await self.async_setup_session()

        # We return the whole payload here so we can check returnCode/requiresMFA
        payload = await self._post_json(
            ENDPOINT_AUTH,
            {"userName": username, "password": password, "_df": ""},
            unwrap_data=False
        )

        mfa_required = (
            payload.get("requiresMFA")
            or str(payload.get("returnCode")) in ("2", "3", "10")
            or payload.get("data", {}).get("status") == "twoFA"
        )

        if mfa_required:
            await self._async_refresh_csrf_from_aft()
            await asyncio.sleep(0.5)  # Wait for CSRF to settle
            await self._async_check_device_token()
            await asyncio.sleep(0.5)  # Wait before asking for methods
            send_methods = await self._async_get_send_methods()
            raise OTPRequiredError(send_methods)

        if str(payload.get("returnCode", "0")) not in ("0", "1"):
            raise InvalidCredentialsError(payload.get("pageMessage", "Login failed"))

        await self.async_get_aft()

    async def async_send_pin(self, send_method: str) -> None:
        """Send the MFA PIN via a lenient POST (sendPIN may return non-JSON on success)."""
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_SEND_PIN,
                headers=self._api_headers,
                json={"sendMethod": send_method, "_df": ""},
                allow_redirects=True,
            ) as resp:
                if resp.status in (401, 403, 555):
                    raise SessionExpiredError(f"Session invalidated (HTTP {resp.status})")
                # sendPIN may return non-JSON on success — do not check content-type
        except SessionExpiredError:
            raise
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

    async def async_verify_pin(self, pin_code: str) -> None:
        """Verify the MFA PIN."""
        payload = await self._post_json(
            ENDPOINT_VERIFY_PIN,
            {"PINcode": pin_code, "registerDevice": True, "_df": ""},
            unwrap_data=False
        )
        if str(payload.get("returnCode", "0")) not in ("0", "1"):
            raise InvalidCredentialsError("Invalid OTP code")
        await self.async_get_aft()

    async def async_get_aft(self) -> None:
        """Public wrapper to refresh the Anti-Forgery Token."""
        await self._async_refresh_csrf_from_aft()

    async def _async_refresh_csrf_from_aft(self) -> None:
        """Internal helper to fetch fresh CSRF token."""
        try:
            async with self._session.get(BASE_URL + ENDPOINT_GET_AFT, headers=self._api_headers) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    token = data.get("data") if isinstance(data.get("data"), str) else data.get("token")
                    if token:
                        self._api_csrf_token = token
        except Exception:
            _LOGGER.debug("CSRF refresh skipped")

    async def _async_check_device_token(self) -> None:
        """Advanced state machine probe."""
        try:
            await self._session.get(BASE_URL + ENDPOINT_VERIFY_2FA_TOKEN, headers=self._api_headers)
        except Exception:
            pass

    async def _async_get_send_methods(self) -> list[dict]:
        """Fetch MFA delivery options and return type+description dicts."""
        await asyncio.sleep(1.2)  # Simulate human "thinking" time

        # Override headers specifically for this sensitive call
        headers = self._api_headers
        headers["Referer"] = f"{BASE_URL}/access/login"

        try:
            async with self._session.get(
                    BASE_URL + ENDPOINT_INIT_AUTH,
                    headers=headers
            ) as resp:
                self._check_session_expired(resp)
                payload = await resp.json(content_type=None)
        except SessionExpiredError:
            _LOGGER.warning("WAF blocked MFA method fetch (HTTP 555). Try again in a few minutes.")
            raise

        # Dominion returns data as a list of dicts:
        # [{"type": "Email", "description": "m***@gmail.com"}, ...]
        data = payload.get("data", [])

        if isinstance(data, list) and len(data) > 0:
            methods = [
                {"type": item["type"], "description": item.get("description", item["type"])}
                for item in data
                if item.get("type")
            ]
            if methods:
                return methods

        # Fallback if the data structure is unexpected
        return [
            {"type": "Email", "description": "Email"},
            {"type": "SMS", "description": "SMS"},
        ]

    def _check_session_expired(self, resp: aiohttp.ClientResponse) -> None:
        """Check for session expiry or WAF blocks."""
        if resp.status in (401, 403, 555):
            raise SessionExpiredError(f"Session invalidated (HTTP {resp.status})")
        if "json" not in (resp.content_type or "").lower():
            raise SessionExpiredError("Server returned non-JSON; possible redirect or block.")

    async def _get_json(self, endpoint: str, params: dict | None = None, unwrap_data: bool = True) -> Any:
        """GET helper with error handling."""
        try:
            async with self._session.get(
                BASE_URL + endpoint,
                headers=self._api_headers,
                params=params
            ) as resp:
                self._check_session_expired(resp)
                payload = await resp.json(content_type=None)
                if unwrap_data:
                    return payload.get("data", payload)
                return payload
        except SessionExpiredError:
            raise
        except Exception as err:
            raise CannotConnectError(str(err)) from err

    async def _post_json(self, endpoint: str, body: dict, unwrap_data: bool = True) -> Any:
        """POST helper with error handling."""
        try:
            async with self._session.post(
                BASE_URL + endpoint,
                headers=self._api_headers,
                json=body
            ) as resp:
                self._check_session_expired(resp)
                payload = await resp.json(content_type=None)
                if unwrap_data:
                    return payload.get("data", payload)
                return payload
        except SessionExpiredError:
            raise
        except Exception as err:
            raise CannotConnectError(str(err)) from err

    # Data Fetching Methods
    async def async_get_account_listing(self) -> dict:
        return await self._get_json(ENDPOINT_LISTING)

    async def async_select_account(self, encrypted_number: str) -> bool:
        await self._post_json(ENDPOINT_SELECT, {"EncryptedAccountNumber": encrypted_number, "_df": ""})
        return True

    async def async_get_account_summary(self) -> dict:
        return await self._get_json(ENDPOINT_ACCOUNT_INIT)

    async def async_get_payment_widget(self) -> dict:
        return await self._get_json(ENDPOINT_PAYMENT)

    async def async_get_energy_analyzer(self) -> dict:
        return await self._get_json(ENDPOINT_ENERGY)

    async def async_get_all_usage_data(self) -> dict:
        return await self._get_json(ENDPOINT_USAGE)

    async def async_get_daily_usage(self, start: str, end: str, revenue_month: int = 0) -> dict:
        params = {"startDate": start, "endDate": end, "revenueMonth": revenue_month, "callType": "L"}
        return await self._get_json(ENDPOINT_DAILY, params=params)