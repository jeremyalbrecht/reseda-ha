"""DataUpdateCoordinator for the Réséda integration."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import CoreState, HomeAssistant
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
    CONF_PASC_REF,
    CONF_PRICE_ENTITY,
    CONF_PRICE_PER_KWH,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    HISTORY_BACKFILL_DAYS,
    SCAN_INTERVAL,
    TZ_PARIS,
)
from .price import (
    PriceStore,
    PriceTimeline,
    async_get_price_points,
    compact_points,
    merge_points,
    state_price,
    union_points,
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
        self._price_fallback_warned = False
        self._price_store_cleared = False
        self.price_store = PriceStore(hass, config_entry.entry_id)

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
        """Find the PASC matching the configured reference.

        The portal's PASC ``id`` is a session-scoped token and changes on every
        login, so we match on the stable contract reference instead.
        """
        target_ref = self.config_entry.data.get(CONF_PASC_REF)
        pascs = await self.client.async_get_pascs()
        if target_ref:
            for candidate in pascs:
                if target_ref in (candidate.pds_reference, candidate.reference):
                    return candidate
        if pascs:
            _LOGGER.warning(
                "Configured PASC %s not found, falling back to %s",
                target_ref,
                pascs[0].pds_reference or pascs[0].reference,
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
        timeline = await self._async_price_timeline(min(total_by_day))
        if timeline is None:
            return
        cost_by_day: dict[date, float] = {}
        for day, kwh in total_by_day.items():
            price = timeline.price_at(_day_start(day))
            if price is not None:
                cost_by_day[day] = kwh * price
        await self._import_one_stream(
            statistic_id=f"{DOMAIN}:{slug}_cost_total",
            name=f"Réséda {pasc.pds_reference} cost",
            unit=currency,
            unit_class=None,
            day_to_value=cost_by_day,
        )

    async def _async_price_timeline(self, first_day: date) -> PriceTimeline | None:
        """Return the prices to apply, or None when no price is configured.

        With a price entity, each day is priced with the value the entity had
        at the start of that day (see ``price.py``). Days before the first
        known value use the fixed ``price_per_kwh`` so previously imported
        costs stay as they were; without a fixed price, the earliest known
        entity value is used.
        """
        options = self.config_entry.options
        fixed: float | None = options.get(CONF_PRICE_PER_KWH) or None
        entity_id: str | None = options.get(CONF_PRICE_ENTITY)
        if not entity_id:
            if not self._price_store_cleared:
                # Forget prices learned for a previously configured entity.
                self._price_store_cleared = True
                await self.price_store.async_remove()
            return PriceTimeline([], fixed) if fixed else None
        self._price_store_cleared = False

        state_points, stat_points = await async_get_price_points(
            self.hass, entity_id, _day_start(first_day), dt_util.utcnow()
        )
        stored = await self.price_store.async_load(entity_id)
        known = compact_points(union_points(stored, state_points))
        if known != stored:
            await self.price_store.async_save(entity_id, known)
        points = merge_points(known, stat_points)
        current = self.hass.states.get(entity_id)
        # During startup the price entity's integration may not be loaded yet.
        starting = current is None and self.hass.state is not CoreState.running
        if state_price(current) is not None:
            self._price_fallback_warned = False
        elif not self._price_fallback_warned and not starting:
            self._price_fallback_warned = True
            if points:
                _LOGGER.warning(
                    "Price entity %s is unavailable or not numeric; using its "
                    "last recorded price %s",
                    entity_id,
                    points[-1].price,
                )
            elif fixed:
                _LOGGER.warning(
                    "Price entity %s is unavailable or not numeric and has no "
                    "recorded history; using the fixed price %s",
                    entity_id,
                    fixed,
                )
            else:
                _LOGGER.warning(
                    "Price entity %s is unavailable or not numeric and no fixed "
                    "price is set; skipping cost statistics",
                    entity_id,
                )
        if not points and not fixed:
            return None
        return PriceTimeline(points, fixed)

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

        # Always recompute and upsert all points so that days initially stored
        # with zero (API data not yet published) get corrected on later runs.
        running_sum = 0.0
        points: list[StatisticData] = []
        for day in sorted(day_to_value):
            start = _day_start(day)
            value = day_to_value[day]
            running_sum += value
            points.append(StatisticData(start=start, state=value, sum=running_sum))

        if not points:
            return
        _LOGGER.debug("Upserting %d statistics for %s", len(points), statistic_id)
        async_add_external_statistics(self.hass, metadata, points)


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


def _day_start(day: date) -> datetime:
    """Midnight (Europe/Paris) of ``day``, in UTC."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=TZ_PARIS)
    return start.astimezone(dt_util.UTC)


def _slugify(value: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in value]
    slug = "".join(keep).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "unknown"
