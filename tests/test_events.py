from scripto.core.events import (
    BufferedSubscriber,
    EventBus,
    LogEvent,
    ProgressEvent,
)


def test_all_subscribers_receive_events():
    bus = EventBus()
    seen_a, seen_b = [], []
    bus.subscribe(seen_a.append)
    bus.subscribe(seen_b.append)
    event = LogEvent(level="info", message="hello")
    bus.emit(event)
    assert seen_a == [event]
    assert seen_b == [event]


def test_unsubscribe_stops_delivery_and_is_idempotent():
    bus = EventBus()
    seen = []
    unsubscribe = bus.subscribe(seen.append)
    bus.emit(LogEvent(level="info", message="one"))
    unsubscribe()
    unsubscribe()  # second call must not raise
    bus.emit(LogEvent(level="info", message="two"))
    assert len(seen) == 1


def test_failing_subscriber_does_not_break_others():
    bus = EventBus()
    seen = []

    def broken(_event):
        raise RuntimeError("boom")

    bus.subscribe(broken)
    bus.subscribe(seen.append)
    bus.emit(LogEvent(level="info", message="still delivered"))
    assert len(seen) == 1


def test_buffered_subscriber_caps_and_counts_drops():
    bus = EventBus()
    buffered = BufferedSubscriber(bus, capacity=3)
    for i in range(5):
        bus.emit(ProgressEvent(scope="x", done=i, total=5))
    assert buffered.dropped == 2
    drained = buffered.drain()
    assert [e.done for e in drained] == [2, 3, 4]  # oldest were dropped
    assert buffered.drain() == []


def test_buffered_subscriber_close_detaches():
    bus = EventBus()
    buffered = BufferedSubscriber(bus, capacity=3)
    buffered.close()
    bus.emit(LogEvent(level="info", message="after close"))
    assert buffered.drain() == []
