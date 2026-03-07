"""Constants for Dominion Energy South Carolina integration."""
from datetime import timedelta

DOMAIN = "dominion_energy_sc"
BASE_URL = "https://account.dominionenergysc.com"
SCAN_INTERVAL = timedelta(hours=12)

# Endpoints
ENDPOINT_LOGIN = "/Access/"
ENDPOINT_HOME = "/"
ENDPOINT_INIT = "/fusionapi/AccountSummaryWebApi/InitAccount/"
ENDPOINT_PAYMENT = "/fusionapi/CommonPaymentsWebApi/GetPaymentWidgetInfo/?paymentWidgetSourcePage=account-summary"
ENDPOINT_BILLING_CHART = "/fusionapi/CommonBillingWebApi/GetAcctBillingHistoryChart/"
ENDPOINT_ENERGY = "/fusionapi/BillingWebApi/GetEnergyAnalyzerDetails/"
ENDPOINT_USAGE = "/fusionapi/BillingWebApi/GetAllUsageData/"
ENDPOINT_DAILY = "/fusionapi/CommonBillingWebApi/GetAccountDailyUsageDetails/"
ENDPOINT_ACCOUNT_TYPE = "/fusionapi/BillingWebApi/GetAccountTypeCode/"
ENDPOINT_LISTING = "/fusionapi/AccountManagementWebApi/GetAccountListing/"
ENDPOINT_SELECT = "/fusionapi/AccountManagementWebApi/SelectAccount/"

# Config keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_COOKIES = "cookies"
