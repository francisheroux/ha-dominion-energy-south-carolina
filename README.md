# Dominion Energy South Carolina Home Assistant Integration

This custom integration enables Home Assistant users to monitor their Dominion Energy South Carolina billing and usage data, including support for both electric and natural gas accounts.

This was created because the Dominion Energy South Carolina uses completely different API endpoints than the Dominion Energy of VA and NC (this repo was inspired by [YeomansIII's version for those states](https://github.com/YeomansIII/ha-dominion-energy))

## Features

- Daily billing data (gas and/or electric) — updated every 12 hours
- Supports multiple accounts under a single login
- Automatic session re-authentication when the portal session expires
- Works with gas-only, electric-only, and dual-service accounts
- HACS-compatible for easy installation and updates

## Installation & Setup

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** → click the three-dot menu → **Custom repositories**
3. Add this repository URL and select **Integration** as the category
4. Search for "Dominion Energy South Carolina" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for "Dominion Energy South Carolina"

### Manual

1. Copy the `custom_components/dominion_energy_sc/` directory into your Home Assistant configuration directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for "Dominion Energy South Carolina"

### Setup Flow

- Enter your Dominion Energy SC email address and password
- The integration will automatically discover all accounts associated with your login
- If multiple accounts are found, you will be prompted to select one
- Each account creates its own device in Home Assistant with associated sensors

## Sensors

| Sensor | Unit | Notes |
|--------|------|-------|
| Current Balance | USD | Amount owed on current bill |
| Due Date | — | Payment due date |
| Last Payment | USD | Most recent payment amount |
| Avg Daily Electric Cost | USD | Electric cost averaged over billing period |
| Avg Daily Electric Usage | kWh | Electric usage averaged over billing period |
| Avg Electric Rate | $/kWh | Effective rate for the billing period |
| Avg Daily Gas Cost | USD | Gas accounts only |
| Avg Daily Gas Usage | CCF | Gas accounts only |
| Avg Gas Rate | $/CCF | Gas accounts only |
| Avg Local Temperature | °F | Average temperature for the billing period |

Electric sensors only appear for electric and dual-service accounts. Gas sensors only appear for gas and dual-service accounts.

## Limitations of the API

- Data updates every 12 hours — near-real-time monitoring is not supported
- Daily granularity only
- Daily usage detail requires a smart meter (AMI meter) on the account

## Support

Report issues via [GitHub Issues](../../issues).

If your Dominion Energy SC credentials change, Home Assistant will automatically prompt you to re-authenticate via the integration's re-auth flow.
