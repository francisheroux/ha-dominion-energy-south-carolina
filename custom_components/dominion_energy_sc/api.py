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
    ENDPOINT_VERIFY_PIN,
)

_LOGGER = logging.getLogger(__name__)


class CannotConnectError(Exception):
    """Cannot connect to Dominion Energy SC portal."""


class InvalidCredentialsError(Exception):
    """Invalid username or password."""


class SessionExpiredError(Exception):
    """Session has expired — coordinator should re-authenticate."""


class ApiError(Exception):
    """Non-zero returnCode from the API."""


class OTPRequiredError(Exception):
    """MFA required; call async_send_pin() after picking a delivery method."""

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
        return {
            "__RequestVerificationToken": self._api_csrf_token or "",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/json",
            "isajax": "true",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE_URL + "/",
        }

    async def async_setup_session(self) -> None:
        """GET the login page to establish session cookies and extract the CSRF token."""
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_ACCESS,
                allow_redirects=True,
            ) as resp:
                html = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        match = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"'
            r'|value="([^"]+)"[^>]*name="__RequestVerificationToken"',
            html,
        )
        if match:
            self._api_csrf_token = match.group(1) or match.group(2)
            _LOGGER.debug("CSRF token extracted from login page")
        else:
            _LOGGER.warning("Could not extract __RequestVerificationToken from /access/")
            # Fallback: read from the __RequestVerificationToken cookie set by /access/
            csrf_cookie = self._session.cookie_jar.filter_cookies(
                BASE_URL + ENDPOINT_ACCESS
            ).get("__RequestVerificationToken")
            if csrf_cookie:
                self._api_csrf_token = csrf_cookie.value
                _LOGGER.debug("CSRF token extracted from cookie jar (fallback)")
            else:
                _LOGGER.warning("CSRF token could not be extracted from HTML or cookies")

    async def async_login(self, username: str, password: str) -> None:
        """Authenticate via JSON REST API.

        Raises OTPRequiredError if MFA is required (with delivery method list).
        Raises InvalidCredentialsError on bad credentials.
        Sets self._api_csrf_token on success.
        """
        await self.async_setup_session()

        _LOGGER.debug(
            "Auth attempt: CSRF token present=%s len=%d",
            bool(self._api_csrf_token),
            len(self._api_csrf_token) if self._api_csrf_token else 0,
        )

        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_AUTH,
                json={"userName": username, "password": password, "_df": ""},
                headers={
                    "__requestverificationtoken": self._api_csrf_token or "",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/json",
                    "isajax": "true",
                    "Origin": BASE_URL,
                    "Referer": BASE_URL + ENDPOINT_ACCESS,
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                _LOGGER.debug("Authenticate HTTP status: %s", resp.status)
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.info("Authenticate response: %s", payload)

        # Detect ASP.NET exception response format {errorNumber, message, detail}
        if "detail" in payload and "Exception" in str(payload.get("detail", "")):
            raise CannotConnectError(
                f"Server error during authentication: {payload.get('message', 'Unknown error')}"
            )
        error_number = payload.get("errorNumber")
        if error_number is not None and int(error_number) != 0:
            raise CannotConnectError(
                f"Server returned errorNumber={error_number}: {payload.get('message')}"
            )

        has_return_code = "returnCode" in payload
        return_code = str(payload.get("returnCode", "-1"))

        # MFA required: returnCode indicates it, or a dedicated mfa/pin field is set
        mfa_required = (
            payload.get("requiresMFA")
            or payload.get("mfaRequired")
            or payload.get("pinRequired")
            or return_code in ("2", "3", "10")  # common MFA codes; expand if needed
            or payload.get("data", {}).get("status") == "twoFA"
        )

        if mfa_required:
            send_methods = await self._async_get_send_methods()
            raise OTPRequiredError(send_methods)

        if return_code not in ("0", "1"):
            # If returnCode was absent, allow through if the server signals success
            # via explicit boolean fields instead.
            explicit_success = (
                payload.get("success") is True
                or payload.get("isAuthenticated") is True
            )
            if not has_return_code and explicit_success:
                pass  # API uses alternative success signaling; proceed
            else:
                _LOGGER.error(
                    "Authenticate failed with returnCode=%s message=%s",
                    return_code if has_return_code else "absent",
                    payload.get("pageMessage"),
                )
                raise InvalidCredentialsError(
                    f"returnCode={return_code}: {payload.get('pageMessage')}"
                )

        # Also treat an explicit success=False as bad credentials
        if payload.get("success") is False or payload.get("isAuthenticated") is False:
            raise InvalidCredentialsError("Authentication rejected by server")

        await self.async_get_aft()

    async def _async_get_send_methods(self) -> list[str]:
        """Call InitAuthentication and return list of masked delivery options."""
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_INIT_AUTH,
                headers={
                    "__requestverificationtoken": self._api_csrf_token or "",
                    "Accept": "application/json",
                    "isajax": "true",
                    "Referer": BASE_URL + ENDPOINT_ACCESS,
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.debug("InitAuthentication response: %s", payload)

        # Response may be list directly or nested under data/sendMethods/etc.
        data = payload.get("data", payload)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("sendMethods", "methods", "deliveryOptions"):
                if key in data:
                    return data[key]
            # userInfo structure: {"phoneNumbers": [...], "emailAddresses": [...]}
            user_info = data.get("userInfo", {})
            if user_info:
                methods = []
                methods.extend(user_info.get("phoneNumbers", []))
                methods.extend(user_info.get("emailAddresses", []))
                if methods:
                    return methods
        # Fallback: return the raw payload as a single-item list so the flow can proceed
        return [str(data)]

    async def async_send_pin(self, send_method: str) -> None:
        """Send OTP to the chosen delivery method."""
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_SEND_PIN,
                json={"sendMethod": send_method, "_df": ""},
                headers=self._api_headers,
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                _LOGGER.debug("SendPINCode response (HTTP %s): %s", resp.status, text[:200])
                if not text.strip():
                    return  # empty body = accepted
                payload = json.loads(text)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        if payload.get("success") is False or payload.get("status") is False:
            raise CannotConnectError(
                f"SendPINCode rejected: {payload.get('pageMessage') or payload}"
            )

    async def async_verify_pin(self, pin_code: str) -> None:
        """Verify OTP and register device to skip MFA on subsequent logins.

        Raises InvalidCredentialsError if the code is wrong/expired.
        """
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_VERIFY_PIN,
                json={"PINcode": pin_code, "registerDevice": True, "_df": ""},
                headers=self._api_headers,
                allow_redirects=True,
            ) as resp:
                text = await resp.text()
                _LOGGER.debug("VerifyPIN response (HTTP %s): %s", resp.status, text[:200])
                if not text.strip():
                    _LOGGER.debug("VerifyPIN returned empty body — treating as success")
                    await self.async_get_aft()
                    return
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    if resp.status < 300:
                        _LOGGER.debug("VerifyPIN returned non-JSON (HTTP %s) — treating as success", resp.status)
                        await self.async_get_aft()
                        return
                    raise CannotConnectError(
                        f"VerifyPIN returned non-JSON response (HTTP {resp.status})"
                    )
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.debug("VerifyPIN response: %s", payload)

        return_code = str(payload.get("returnCode", "0"))
        if return_code not in ("0", "1"):
            raise InvalidCredentialsError(
                f"VerifyPIN failed returnCode={return_code}: {payload.get('pageMessage')}"
            )

        if payload.get("success") is False or payload.get("isValid") is False:
            raise InvalidCredentialsError("OTP verification rejected by server")

        await self.async_get_aft()

    async def async_get_aft(self) -> None:
        """Call GetAFT for protocol compliance (token comes from /access/ page, not here)."""
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_GET_AFT,
                headers={
                    "__requestverificationtoken": self._api_csrf_token or "",
                    "Accept": "application/json",
                    "isajax": "true",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err
        _LOGGER.debug("GetAFT response: %s", payload)

    def _check_session_expired(self, resp: aiohttp.ClientResponse) -> None:
        """Raise SessionExpiredError if response indicates session loss."""
        content_type = resp.content_type or ""
        if "json" not in content_type:
            raise SessionExpiredError(
                f"Expected JSON but got content-type: {content_type}"
            )
        final_url = str(resp.url)
        if ENDPOINT_AUTH.rstrip("/") in final_url:
            raise SessionExpiredError("API call redirected to login endpoint")

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
