"""Resolve the €/kWh price in force at a given time from a Home Assistant entity.

The price entity (e.g. a regulated-tariff sensor) changes a few times a year.
To price each imported day with the tariff that applied on that day, we rebuild
a timeline of price changes from:

1. the recorder state history of the entity (short-term, ~10 days by default),
   which carries attributes — a ``valid_from`` attribute, when present, is used
   as the effective date of that price instead of the time the state changed;
2. the entity's long-term hourly statistics (``mean``), kept indefinitely for
   sensors with a ``state_class``, used for the period before the state history;
3. the entity's current state.

State-derived points are also persisted (see ``PriceStore``) so the precise
``valid_from`` dates survive the recorder purging old states.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
import logging

from homeassistant.components.recorder import get_instance, history, statistics
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import ATTR_VALID_FROM, DOMAIN, TZ_PARIS

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A price that applies from ``effective`` (UTC) until the next point."""

    effective: datetime
    price: float


class PriceTimeline:
    """Step function of prices over time."""

    def __init__(self, points: list[PricePoint], before_first: float | None) -> None:
        """Initialize from points sorted by ``effective``.

        ``before_first`` is used for times earlier than the first known point;
        when ``None`` the earliest known price is used instead.
        """
        self.points = points
        self._starts = [p.effective for p in points]
        self._before_first = before_first

    def __bool__(self) -> bool:
        """Return True when at least one price is known."""
        return bool(self.points)

    def price_at(self, when: datetime) -> float | None:
        """Return the price in force at ``when``."""
        idx = bisect_right(self._starts, when)
        if idx:
            return self.points[idx - 1].price
        if self._before_first is not None:
            return self._before_first
        return self.points[0].price if self.points else None


def state_price(state: State | None) -> float | None:
    """Return the numeric price of a state, or None if unusable."""
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        price = float(state.state)
    except ValueError:
        return None
    return price if price >= 0 else None


def _effective_from(state: State) -> datetime:
    """When the price carried by ``state`` started to apply."""
    raw = state.attributes.get(ATTR_VALID_FROM)
    parsed: datetime | None = None
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, date):
        parsed = datetime.combine(raw, datetime.min.time())
    elif isinstance(raw, str) and raw:
        parsed = dt_util.parse_datetime(raw)
        if parsed is None and (day := dt_util.parse_date(raw)) is not None:
            parsed = datetime.combine(day, datetime.min.time())
    if parsed is None:
        return state.last_changed
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ_PARIS)
    return dt_util.as_utc(parsed)


def _points_from_states(states: list[State]) -> list[PricePoint]:
    points: list[PricePoint] = []
    for state in states:
        if (price := state_price(state)) is not None:
            points.append(PricePoint(_effective_from(state), price))
    return points


def _points_from_statistics(rows: list[dict]) -> list[PricePoint]:
    points: list[PricePoint] = []
    for row in rows:
        mean = row.get("mean")
        if mean is None or mean < 0:
            continue
        points.append(PricePoint(dt_util.utc_from_timestamp(row["start"]), mean))
    return points


def union_points(old: list[PricePoint], new: list[PricePoint]) -> list[PricePoint]:
    """Union two point lists by effective time (``new`` wins), sorted."""
    by_time = {p.effective: p for p in old}
    by_time.update((p.effective, p) for p in new)
    return [by_time[key] for key in sorted(by_time)]


def merge_points(
    state_points: list[PricePoint], stat_points: list[PricePoint]
) -> list[PricePoint]:
    """Combine both sources into a sorted list of price changes.

    State-derived points are authoritative (they carry ``valid_from``); hourly
    statistics only fill the period before the earliest state-derived point.
    """
    if state_points:
        cutoff = min(p.effective for p in state_points)
        stat_points = [p for p in stat_points if p.effective < cutoff]
    merged = sorted(stat_points + state_points, key=lambda p: p.effective)
    changes: list[PricePoint] = []
    for point in merged:
        if changes and changes[-1].price == point.price:
            continue
        changes.append(point)
    return changes


async def async_get_price_points(
    hass: HomeAssistant, entity_id: str, start: datetime, end: datetime
) -> tuple[list[PricePoint], list[PricePoint]]:
    """Return (state-derived, statistics-derived) points for ``entity_id``."""
    recorder = get_instance(hass)
    states: list[State] = []
    stat_rows: list[dict] = []
    try:
        changes = await recorder.async_add_executor_job(
            history.state_changes_during_period,
            hass,
            start,
            end,
            entity_id,
            False,  # no_attributes: we need valid_from
            False,  # descending
            None,  # limit
            True,  # include_start_time_state
        )
        states = list(changes.get(entity_id, []))
        stats = await recorder.async_add_executor_job(
            statistics.statistics_during_period,
            hass,
            start,
            end,
            {entity_id},
            "hour",
            None,
            {"mean"},
        )
        stat_rows = list(stats.get(entity_id, []))
    except Exception:  # noqa: BLE001 - history is best effort
        _LOGGER.debug("Could not read history of %s", entity_id, exc_info=True)

    if (current := hass.states.get(entity_id)) is not None:
        states.append(current)
    return _points_from_states(states), _points_from_statistics(stat_rows)


class PriceStore:
    """Persist the state-derived price points of the configured entity."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize."""
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry_id}.prices")

    async def async_load(self, entity_id: str) -> list[PricePoint]:
        """Return the stored points, or [] if they belong to another entity."""
        data = await self._store.async_load()
        if not data or data.get("entity_id") != entity_id:
            return []
        points: list[PricePoint] = []
        for raw_time, price in data.get("points", []):
            if (when := dt_util.parse_datetime(raw_time)) is not None:
                points.append(PricePoint(dt_util.as_utc(when), float(price)))
        return sorted(points, key=lambda p: p.effective)

    async def async_save(self, entity_id: str, points: list[PricePoint]) -> None:
        """Store the points."""
        await self._store.async_save(
            {
                "entity_id": entity_id,
                "points": [[p.effective.isoformat(), p.price] for p in points],
            }
        )

    async def async_remove(self) -> None:
        """Delete the stored file."""
        await self._store.async_remove()
