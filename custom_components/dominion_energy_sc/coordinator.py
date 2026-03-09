"""DataUpdateCoordinator for Dominion Energy South Carolina."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    CannotConnectError,
    DominionEnergySCClient,
    InvalidCredentialsError,
    OTPRequiredError,
    SessionExpiredError,
)
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class AccountData:
    """Data for a single Dominion Energy SC account."""

    account_number: str  # encrypted opaque ID
    display_number: str  # "*-****-***4-5678"
    address: str
    balance: float
    due_date: str
    last_payment: float
    has_electric: bool
    has_gas: bool
    # Latest bill month values
    avg_daily_electric_cost: float
    avg_daily_electric_usage: float  # kWh/day
    avg_per_unit_electric: float  # $/kWh
    avg_daily_gas_cost: float
    avg_daily_gas_usage: float  # CCF/day
    avg_per_unit_gas: float  # $/CCF
    avg_temp: float  # °F
    # Daily arrays (current billing period)
    daily_electric_kwh: list[float] = field(default_factory=list)
    daily_gas_ccf: list[float] = field(default_factory=list)
    daily_dates: list[str] = field(default_factory=list)
    # Account status
    is_closed: bool = False
    has_ami_meter: bool = False


def _parse_currency(value: str | float | None) -> float:
    """Parse a currency string like '$98.12' to float."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace("$", "").replace(",", "").strip() or 0)


class DominionEnergySCCoordinator(DataUpdateCoordinator[dict[str, AccountData]]):
    """Coordinator that polls Dominion Energy SC and returns per-account data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self._entry = entry
        self._cookie_jar = aiohttp.CookieJar()
        self._session: aiohttp.ClientSession | None = None
        self._client: DominionEnergySCClient | None = None

    def _build_session(self) -> None:
        """Create a new aiohttp session with our cookie jar."""
        if self._session:
            self.hass.async_create_task(self._session.close())
        self._session = aiohttp.ClientSession(
            cookie_jar=self._cookie_jar,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"},
        )
        self._client = DominionEnergySCClient(self._session)

    async def _async_login(self) -> None:
        """Build session (if needed) and perform login."""
        if self._session is None or self._session.closed:
            self._build_session()
        username = self._entry.data[CONF_USERNAME]
        password = self._entry.data[CONF_PASSWORD]
        await self._client.async_login(username, password)

    async def _async_fetch_account_data(
        self, encrypted_number: str, account_info: dict
    ) -> AccountData:
        """Fetch all data for a single account (already selected)."""
        summary_payload = await self._client.async_get_account_summary()
        account = summary_payload.get("account", {})
        balance = _parse_currency(account.get("accountBalance"))
        due_date = account.get("dueDate", "")
        last_payment = _parse_currency(account.get("lastPaymentAmount"))
        address_field = account.get("serviceAddressAndAccountNo", "")
        # Strip the masked account number from the address field if present
        # Format: "123 MAIN ST (*-****-***4-5678)"
        address = address_field.split("(")[0].strip()
        display_number = account_info.get("accountNumberFormatted", "")
        if not display_number and "(" in address_field:
            display_number = address_field.split("(")[1].rstrip(")")

        is_closed = account_info.get("closedAccount", False) or account_info.get(
            "accountClosed", False
        )
        has_ami = account_info.get("hasAMIMeter", False)

        # Energy analyzer — primary usage endpoint
        energy_payload = {}
        has_electric = False
        has_gas = False
        avg_daily_elec_cost = 0.0
        avg_daily_elec_usage = 0.0
        avg_per_unit_elec = 0.0
        avg_daily_gas_cost = 0.0
        avg_daily_gas_usage = 0.0
        avg_per_unit_gas = 0.0
        avg_temp = 0.0

        try:
            energy_payload = await self._client.async_get_energy_analyzer()
            bill_months = energy_payload.get("allBillMonthDetails", [])
            if bill_months:
                latest = bill_months[-1]
                has_electric = bool(latest.get("hasElectricService", False))
                has_gas = bool(latest.get("hasGasService", False))
                avg_daily_elec_cost = float(
                    latest.get("avgDailyCostElectricService", 0.0)
                )
                avg_daily_elec_usage = float(
                    latest.get("avgDailyUsageElectricService", 0.0)
                )
                avg_per_unit_elec = float(
                    latest.get("avgPerUnitCostElectricService", 0.0)
                )
                avg_daily_gas_cost = float(latest.get("avgDailyCostGasService", 0.0))
                avg_daily_gas_usage = float(
                    latest.get("avgDailyUsageGasService", 0.0)
                )
                avg_per_unit_gas = float(latest.get("avgPerUnitCostGasService", 0.0))
                avg_temp = float(latest.get("avgLocalTemperature", 0.0))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch energy analyzer for account: %s", err)

        # Daily usage (current billing period)
        daily_electric_kwh: list[float] = []
        daily_gas_ccf: list[float] = []
        daily_dates: list[str] = []

        if has_ami and not is_closed:
            try:
                daily = await self._client.async_get_daily_usage()
                labels = daily.get("xAxisLabelForElectric", [])
                daily_electric_kwh = [
                    float(v) for v in daily.get("barChartElectricUsage", [])
                ]
                daily_gas_ccf = [
                    float(v) for v in daily.get("barChartGasUsage", [])
                ]
                daily_dates = [
                    f"{lbl[0]}/{lbl[1]}" if isinstance(lbl, list) else str(lbl)
                    for lbl in labels
                ]
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Daily usage unavailable: %s", err)

        return AccountData(
            account_number=encrypted_number,
            display_number=display_number,
            address=address,
            balance=balance,
            due_date=due_date,
            last_payment=last_payment,
            has_electric=has_electric,
            has_gas=has_gas,
            avg_daily_electric_cost=avg_daily_elec_cost,
            avg_daily_electric_usage=avg_daily_elec_usage,
            avg_per_unit_electric=avg_per_unit_elec,
            avg_daily_gas_cost=avg_daily_gas_cost,
            avg_daily_gas_usage=avg_daily_gas_usage,
            avg_per_unit_gas=avg_per_unit_gas,
            avg_temp=avg_temp,
            daily_electric_kwh=daily_electric_kwh,
            daily_gas_ccf=daily_gas_ccf,
            daily_dates=daily_dates,
            is_closed=is_closed,
            has_ami_meter=has_ami,
        )

    async def _async_update_data(self) -> dict[str, AccountData]:
        """Fetch data for all accounts."""
        if self._client is None:
            await self._async_login()

        try:
            return await self._async_do_update()
        except SessionExpiredError:
            _LOGGER.debug("Session expired, re-authenticating")
            try:
                await self._async_login()
                return await self._async_do_update()
            except InvalidCredentialsError as err:
                raise ConfigEntryAuthFailed("Credentials invalid after re-auth") from err
            except OTPRequiredError as err:
                raise ConfigEntryAuthFailed("MFA required — please re-authenticate") from err
            except (CannotConnectError, SessionExpiredError) as err:
                raise UpdateFailed(f"Re-auth failed: {err}") from err
        except OTPRequiredError as err:
            raise ConfigEntryAuthFailed("MFA required — please re-authenticate") from err
        except InvalidCredentialsError as err:
            raise ConfigEntryAuthFailed("Invalid credentials") from err
        except CannotConnectError as err:
            raise UpdateFailed(f"Cannot connect: {err}") from err

    async def _async_do_update(self) -> dict[str, AccountData]:
        """Perform the actual data fetch — split out for re-auth retry."""
        listing = await self._client.async_get_account_listing()

        accounts: list[dict] = []
        if listing.get("singleAccount", True):
            # Single account — use minimal info; full data comes from InitAccount
            accounts = [
                {
                    "accountNumber": listing.get("accountNumber", ""),
                    "accountNumberFormatted": listing.get(
                        "accountNumberFormatted", ""
                    ),
                    "accountStatus": "A",
                    "closedAccount": False,
                    "hasAMIMeter": listing.get("hasAMIMeter", False),
                }
            ]
        else:
            accounts = listing.get("accountListing", [])

        result: dict[str, AccountData] = {}
        for acct in accounts:
            enc_num = acct.get("accountNumber", "")
            if not enc_num:
                continue

            # Select this account for subsequent API calls
            try:
                await self._client.async_select_account(enc_num)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not select account %s: %s", enc_num[:8], err)
                continue

            try:
                account_data = await self._async_fetch_account_data(enc_num, acct)
                result[enc_num] = account_data
            except SessionExpiredError:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to fetch data for account %s: %s", enc_num[:8], err
                )

        if not result:
            raise UpdateFailed("No account data could be retrieved")

        return result
