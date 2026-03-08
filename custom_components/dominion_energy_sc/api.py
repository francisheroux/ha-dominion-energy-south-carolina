"""Dominion Energy South Carolina API client."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
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

    async def async_login(self, username: str, password: str) -> None:
        """Authenticate via JSON REST API.

        Raises OTPRequiredError if MFA is required (with delivery method list).
        Raises InvalidCredentialsError on bad credentials.
        Sets self._api_csrf_token on success.
        """
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_AUTH,
                json={"userName": username, "password": password, "_df": ""},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.debug("Authenticate response: %s", payload)

        return_code = str(payload.get("returnCode", "0"))

        # MFA required: returnCode indicates it, or a dedicated mfa/pin field is set
        mfa_required = (
            payload.get("requiresMFA")
            or payload.get("mfaRequired")
            or payload.get("pinRequired")
            or return_code in ("2", "3", "10")  # common MFA codes; expand if needed
        )

        if mfa_required:
            send_methods = await self._async_get_send_methods()
            raise OTPRequiredError(send_methods)

        if return_code not in ("0", "1"):
            _LOGGER.error(
                "Authenticate failed with returnCode=%s message=%s",
                return_code,
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
                    "Accept": "application/json",
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
        # Fallback: return the raw payload as a single-item list so the flow can proceed
        return [str(data)]

    async def async_send_pin(self, send_method: str) -> None:
        """Send OTP to the chosen delivery method."""
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_SEND_PIN,
                json={"sendMethod": send_method, "_df": ""},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.debug("SendPINCode response: %s", payload)

    async def async_verify_pin(self, pin_code: str) -> None:
        """Verify OTP and register device to skip MFA on subsequent logins.

        Raises InvalidCredentialsError if the code is wrong/expired.
        """
        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_VERIFY_PIN,
                json={"PINcode": pin_code, "registerDevice": True, "_df": ""},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
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
        """Retrieve the anti-forgery token and store it for API calls."""
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_GET_AFT,
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allow_redirects=True,
            ) as resp:
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        _LOGGER.debug("GetAFT response: %s", payload)

        # Token may be at top-level "data", nested, or a bare string
        data = payload.get("data", payload)
        if isinstance(data, str):
            self._api_csrf_token = data
        elif isinstance(data, dict):
            for key in ("token", "aft", "antiForgerToken", "__RequestVerificationToken"):
                if key in data:
                    self._api_csrf_token = data[key]
                    break
            else:
                # Last resort: use the first string value found
                for v in data.values():
                    if isinstance(v, str) and v:
                        self._api_csrf_token = v
                        break
        else:
            _LOGGER.warning("Unexpected GetAFT payload shape: %s", payload)

        _LOGGER.debug("AFT token set: %s", bool(self._api_csrf_token))

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
