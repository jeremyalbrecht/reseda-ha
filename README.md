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

## Cost statistic

Because the statistics are imported (not a live sensor), the Energy dashboard
cannot apply a price entity to them. Instead the integration publishes its own
cost statistic `reseda:<prm>_cost_total`, configured in **Settings → Devices &
Services → Réséda → Configure**:

- **Fixed price per kWh** — applied to every day.
- **Price entity** (optional, `sensor` or `input_number`, in €/kWh) — for
  example `sensor.prix_energie_electricite_prix_electricite` from the
  *Prix Énergie FR* integration. Each day is priced with the value the entity
  had at the start of that day, read from the recorder: state history (a
  `valid_from` attribute, when present, sets the date the price applies from),
  then the entity's long-term statistics for older periods. Prices seen in the
  state history are also saved by the integration, so their exact dates are
  kept after the recorder purges old states. Days before the
  entity's first known value keep the fixed price. If the entity is
  unavailable, its last recorded value is used, then the fixed price; a
  warning is logged once.

Costs are recomputed for the whole imported history on every update, so they
always follow the known price history. Sensors without a `state_class` (and
`input_number` helpers) have no long-term statistics, so prices older than
the recorder's state history (10 days by default) and from before you set the
option are unknown unless the entity sets `valid_from`.

## Troubleshooting

- *Invalid authentication*: try logging in via the browser; if that works,
  capture a HAR of the failing config-flow request and open an issue.
- Backfill imports two years of daily data on first run; subsequent polls only
  fetch the delta.

## Disclaimer

This integration is not affiliated with Réséda or UEM. It reverse-engineers
the public customer portal. Endpoints may change without notice.
