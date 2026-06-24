"""DataUpdateCoordinator for the Réséda integration."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import (
    Address,
    DailyReading,
    PascSummary,
    ResedaApiError,
    ResedaAuthError,
    ResedaClient,
    ResedaConnectionError,
)
from .const import (
    CONF_PASC_ID,
    CONF_PRICE_PER_KWH,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    HISTORY_BACKFILL_DAYS,
    SCAN_INTERVAL,
    TZ_PARIS,
)

_LOGGER = logging.getLogger(__name__)

type ResedaConfigEntry = ConfigEntry["ResedaCoordinator"]


@dataclass(slots=True)
class ResedaData:
    """Coordinator data exposed to sensors."""

    pasc: PascSummary
    address: Address | None
    last_reading_date: date | None
    consumption_total_kwh: float | None


class ResedaCoordinator(DataUpdateCoordinator[ResedaData]):
    """Polls daily consumption and pushes it into long-term statistics."""

    config_entry: ResedaConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ResedaConfigEntry,
        client: ResedaClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            config_entry=config_entry,
        )
        self.client = client

    async def _async_update_data(self) -> ResedaData:
        try:
            pasc = await self._resolve_pasc()
            address = await self._fetch_address(pasc)
            now = dt_util.utcnow()
            start = now - timedelta(days=HISTORY_BACKFILL_DAYS)
            readings = await self.client.async_get_history(pasc.id, start, now)
        except ResedaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (ResedaApiError, ResedaConnectionError) as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._persist_refresh_token()

        await self._async_import_statistics(pasc, readings)
        return _summarise(pasc, address, readings)

    async def _resolve_pasc(self) -> PascSummary:
        """Find the PASC matching the configured ID; fall back to the first one."""
        target_id = self.config_entry.data[CONF_PASC_ID]
        pascs = await self.client.async_get_pascs()
        for candidate in pascs:
            if candidate.id == target_id:
                return candidate
        if pascs:
            _LOGGER.warning(
                "Configured PASC %s not found, falling back to %s",
                target_id,
                pascs[0].id,
            )
            return pascs[0]
        raise UpdateFailed("No PASCs available for this account")

    async def _fetch_address(self, pasc: PascSummary) -> Address | None:
        if not pasc.delivery_space_id:
            return None
        try:
            return await self.client.async_get_address(pasc.delivery_space_id)
        except (ResedaApiError, ResedaConnectionError) as err:
            _LOGGER.debug("Could not fetch address: %s", err)
            return None

    def _persist_refresh_token(self) -> None:
        token = self.client.refresh_token
        if not token:
            return
        existing = self.config_entry.data.get(CONF_REFRESH_TOKEN)
        if existing == token:
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_REFRESH_TOKEN: token,
            },
        )

    async def _async_import_statistics(
        self, pasc: PascSummary, readings: list[DailyReading]
    ) -> None:
        """Push the aggregated daily kWh (and €, if a price is set) to recorder."""
        if not readings:
            return

        slug = _slugify(pasc.pds_reference or pasc.reference or pasc.id)
        price = self.config_entry.options.get(CONF_PRICE_PER_KWH)
        currency = self.hass.config.currency

        total_by_day: dict[date, float] = defaultdict(float)
        for reading in readings:
            total_by_day[reading.day] += reading.consumption_kwh

        await self._import_one_stream(
            statistic_id=f"{DOMAIN}:{slug}_consumption_total",
            name=f"Réséda {pasc.pds_reference} consumption",
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            unit_class=EnergyConverter.UNIT_CLASS,
            day_to_value=dict(total_by_day),
        )
        if price:
            await self._import_one_stream(
                statistic_id=f"{DOMAIN}:{slug}_cost_total",
                name=f"Réséda {pasc.pds_reference} cost",
                unit=currency,
                unit_class=None,
                day_to_value={d: v * price for d, v in total_by_day.items()},
            )

    async def _import_one_stream(
        self,
        *,
        statistic_id: str,
        name: str,
        unit: str,
        unit_class: str | None,
        day_to_value: dict[date, float],
    ) -> None:
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=name,
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )

        last_stat = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum", "start"}
        )

        running_sum = 0.0
        last_start_ts: float | None = None
        if last_stat and last_stat.get(statistic_id):
            record = last_stat[statistic_id][0]
            running_sum = float(record.get("sum") or 0.0)
            last_start_ts = record.get("start")

        new_points: list[StatisticData] = []
        for day in sorted(day_to_value):
            start = datetime.combine(day, datetime.min.time(), tzinfo=TZ_PARIS)
            start = start.astimezone(dt_util.UTC)
            if last_start_ts is not None and start.timestamp() <= last_start_ts:
                continue
            value = day_to_value[day]
            running_sum += value
            new_points.append(StatisticData(start=start, state=value, sum=running_sum))

        if not new_points:
            return
        _LOGGER.debug("Adding %d statistics for %s", len(new_points), statistic_id)
        async_add_external_statistics(self.hass, metadata, new_points)


def _summarise(
    pasc: PascSummary, address: Address | None, readings: list[DailyReading]
) -> ResedaData:
    if not readings:
        return ResedaData(
            pasc=pasc,
            address=address,
            last_reading_date=None,
            consumption_total_kwh=None,
        )

    # Sum the latest index per poste — gives the true cumulative kWh recorded
    # by the meter (each tariff poste has its own register).
    latest_per_poste: dict[str, float] = {}
    for reading in readings:
        latest_per_poste[reading.poste_mnemo] = reading.index_kwh
    return ResedaData(
        pasc=pasc,
        address=address,
        last_reading_date=readings[-1].day,
        consumption_total_kwh=sum(latest_per_poste.values()),
    )


def _slugify(value: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in value]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "unknown"
