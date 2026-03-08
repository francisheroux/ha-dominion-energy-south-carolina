"""Constants for Dominion Energy South Carolina integration."""
from datetime import timedelta

DOMAIN = "dominion_energy_sc"
BASE_URL = "https://account.dominionenergysc.com"
SCAN_INTERVAL = timedelta(hours=12)

# Auth endpoints (JSON REST API)
ENDPOINT_ACCESS = "/access/"
ENDPOINT_AUTH = "/fusionapi/LoginWebApi/Authenticate/"
ENDPOINT_INIT_AUTH = "/fusionapi/LoginWebApi/InitAuthentication/"
ENDPOINT_SEND_PIN = "/fusionapi/LoginWebApi/SendPINCode/"
ENDPOINT_VERIFY_PIN = "/fusionapi/LoginWebApi/VerifyPIN/"
ENDPOINT_GET_AFT = "/fusionapi/LoginWebApi/GetAFT/"

# Account/billing endpoints
ENDPOINT_ACCOUNT_INIT = "/fusionapi/AccountSummaryWebApi/InitAccount/"
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
