"""Unit tests for the price timeline helpers."""

from datetime import UTC, datetime

import pytest

from custom_components.reseda.price import (
    PricePoint,
    PriceTimeline,
    _effective_from,
    merge_points,
    state_price,
)
from homeassistant.core import State


def _dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_price_at_steps() -> None:
    """The price in force is the last point at or before the given time."""
    timeline = PriceTimeline(
        [PricePoint(_dt(2025, 2, 1), 0.2016), PricePoint(_dt(2025, 8, 1), 0.1952)],
        before_first=0.25,
    )
    assert timeline.price_at(_dt(2025, 1, 31)) == 0.25
    assert timeline.price_at(_dt(2025, 2, 1)) == 0.2016
    assert timeline.price_at(_dt(2025, 7, 31, 23)) == 0.2016
    assert timeline.price_at(_dt(2026, 1, 1)) == 0.1952


def test_price_before_first_without_fixed() -> None:
    """Without a fixed price the earliest known price covers earlier days."""
    timeline = PriceTimeline([PricePoint(_dt(2025, 8, 1), 0.19)], before_first=None)
    assert timeline.price_at(_dt(2024, 1, 1)) == 0.19
    assert PriceTimeline([], None).price_at(_dt(2024, 1, 1)) is None
    assert PriceTimeline([], 0.2).price_at(_dt(2024, 1, 1)) == 0.2


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.2001", 0.2001), ("unavailable", None), ("unknown", None), ("abc", None)],
)
def test_state_price(value: str, expected: float | None) -> None:
    """Only numeric states yield a price."""
    assert state_price(State("sensor.price", value)) == expected
    assert state_price(None) is None


def test_effective_from_uses_valid_from() -> None:
    """valid_from (a Paris-local date) wins over last_changed."""
    changed = _dt(2026, 8, 3, 10)
    state = State(
        "sensor.price",
        "0.2001",
        {"valid_from": "2026-08-01"},
        last_changed=changed,
    )
    assert _effective_from(state) == _dt(2026, 7, 31, 22)
    plain = State("sensor.price", "0.2001", last_changed=changed)
    assert _effective_from(plain) == changed


def test_merge_prefers_states_and_dedupes() -> None:
    """Statistics only fill the period before the state history."""
    stats = [
        PricePoint(_dt(2025, 1, 1), 0.25),
        PricePoint(_dt(2025, 1, 1, 1), 0.25),
        PricePoint(_dt(2025, 2, 1), 0.2016),
        # Lagging sensor: statistics still show the old price after valid_from.
        PricePoint(_dt(2025, 8, 1), 0.2016),
        PricePoint(_dt(2025, 8, 3), 0.1952),
    ]
    states = [PricePoint(_dt(2025, 7, 31, 22), 0.1952)]
    assert merge_points(states, stats) == [
        PricePoint(_dt(2025, 1, 1), 0.25),
        PricePoint(_dt(2025, 2, 1), 0.2016),
        PricePoint(_dt(2025, 7, 31, 22), 0.1952),
    ]
