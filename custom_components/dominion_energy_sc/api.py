"""Dominion Energy South Carolina API client."""
from __future__ import annotations

import re
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
    ENDPOINT_DAILY,
    ENDPOINT_ENERGY,
    ENDPOINT_HOME,
    ENDPOINT_INIT,
    ENDPOINT_LISTING,
    ENDPOINT_LOGIN,
    ENDPOINT_PAYMENT,
    ENDPOINT_SELECT,
    ENDPOINT_USAGE,
)


class CannotConnectError(Exception):
    """Cannot connect to Dominion Energy SC portal."""


class InvalidCredentialsError(Exception):
    """Invalid username or password."""


class SessionExpiredError(Exception):
    """Session has expired — coordinator should re-authenticate."""


class ApiError(Exception):
    """Non-zero returnCode from the API."""


def _extract_csrf_token(html: str) -> str | None:
    """Extract __RequestVerificationToken from HTML hidden input or meta tag."""
    match = re.search(
        r'name="__RequestVerificationToken"[^>]+value="([^"]+)"', html
    )
    if match:
        return match.group(1)
    # Also try meta tag variant
    match = re.search(
        r'name="RequestVerificationToken"\s+content="([^"]+)"', html
    )
    if match:
        return match.group(1)
    return None


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
        """Three-phase ASP.NET forms auth login."""
        # Phase 1: GET login page, extract CSRF token + session cookie
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_LOGIN,
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    raise CannotConnectError(
                        f"Login page returned HTTP {resp.status}"
                    )
                html = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        login_csrf = _extract_csrf_token(html)
        if not login_csrf:
            raise CannotConnectError("Could not find CSRF token on login page")

        # Phase 2: POST credentials
        form_data = aiohttp.FormData()
        form_data.add_field("UserName", username)
        form_data.add_field("Password", password)
        form_data.add_field("__RequestVerificationToken", login_csrf)

        try:
            async with self._session.post(
                BASE_URL + ENDPOINT_LOGIN,
                data=form_data,
                allow_redirects=True,
            ) as resp:
                final_url = str(resp.url)
                html_after = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        # If we ended up back at the login page, credentials are wrong
        if ENDPOINT_LOGIN.rstrip("/") in final_url.rstrip("/"):
            raise InvalidCredentialsError("Redirected back to login — bad credentials")

        # Phase 3: GET home page for API CSRF token
        try:
            async with self._session.get(
                BASE_URL + ENDPOINT_HOME,
                allow_redirects=True,
            ) as resp:
                home_html = await resp.text()
        except aiohttp.ClientError as err:
            raise CannotConnectError(str(err)) from err

        api_token = _extract_csrf_token(home_html)
        if not api_token:
            # If we can't get the API token, we probably got redirected to login
            raise SessionExpiredError("Could not extract API CSRF token from home page")

        self._api_csrf_token = api_token

    def _check_session_expired(self, resp: aiohttp.ClientResponse) -> None:
        """Raise SessionExpiredError if response indicates session loss."""
        content_type = resp.content_type or ""
        if "json" not in content_type:
            raise SessionExpiredError(
                f"Expected JSON but got content-type: {content_type}"
            )
        final_url = str(resp.url)
        if ENDPOINT_LOGIN.rstrip("/") in final_url:
            raise SessionExpiredError("API call redirected to login page")

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
        return await self._get_json(ENDPOINT_INIT)

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
