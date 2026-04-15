from intraday_auto_trading.services.tracker import FifteenMinuteTracker


def test_tracker_places_limit_order_after_confirmation() -> None:
    tracker = FifteenMinuteTracker(confirmation_bars=2, limit_price_factor=1.01)

    first = tracker.observe(100.0)
    second = tracker.observe(101.0)
    third = tracker.observe(102.0)

    assert first.should_cancel_order is True
    assert second.should_place_order is False
    assert third.should_place_order is True
    assert third.limit_price == 101.0


def test_tracker_force_buy_uses_lowest_close() -> None:
    tracker = FifteenMinuteTracker(confirmation_bars=2, limit_price_factor=1.01)

    tracker.observe(98.5)

    assert tracker.force_buy_price() == 99.48
