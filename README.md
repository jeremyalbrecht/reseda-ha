# Réséda — Home Assistant integration

<p align="center">
  <img src="custom_components/reseda/brand/logo.png" alt="Réséda" height="96">
</p>

A HACS custom integration that pulls electricity consumption from the
[Réséda](https://monagence.reseda.fr/) customer portal (used by UEM Metz and
other French local DSOs) and feeds it into Home Assistant's Energy dashboard.

## Features

- Username + password authentication, refreshed transparently.
- One config entry per contract (PASC) — pick the right one during setup.
- Daily kWh statistics per tariff poste (HPH / HCH / HPB / HCB) plus an
  aggregate "total" stream wired into the Energy dashboard.
- Sensors for the latest meter index, last reading date, address, tariff
  calendar and contract start.

## Install

1. In HACS → Integrations → ⋮ → Custom repositories, add this repository as
   *Integration*.
2. Install **Réséda** and restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → search "Réséda".
4. Enter your monagence.reseda.fr email and password, then pick the contract.

## Energy dashboard

After the first successful update (within ~6 hours, or trigger a reload), the
statistic IDs `reseda:<prm>_consumption_total` and per-poste variants become
available in **Settings → Dashboards → Energy → Add consumption**.

## Troubleshooting

- *Invalid authentication*: try logging in via the browser; if that works,
  capture a HAR of the failing config-flow request and open an issue.
- Backfill imports two years of daily data on first run; subsequent polls only
  fetch the delta.

## Disclaimer

This integration is not affiliated with Réséda or UEM. It reverse-engineers
the public customer portal. Endpoints may change without notice.
