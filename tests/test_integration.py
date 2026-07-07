"""Tests for the smart-home integration module (CARD-M1).

Fully mocked: a FakeMqttClient captures subscribe/publish and lets the test
drive on_message / on_disconnect callbacks. No real broker; runs on any OS.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from stavau.core.integration import (
    PRESENT_RSSI_DBM,
    ExternalPresenceSource,
    MqttNotifier,
    MqttPresenceBackend,
    NullNotifier,
    make_notifier,
    make_presence_backend,
    parse_present_values,
)


class FakeMessage:
    def __init__(self, payload: bytes | str) -> None:
        self.payload = payload


class FakeMqttClient:
    """Captures MQTT calls and lets tests fire the registered callbacks."""

    def __init__(self) -> None:
        self.subscribed: list[str] = []
        self.published: list[tuple[str, str, int, bool]] = []
        self.connected: list[tuple[str, int]] = []
        self.credentials: tuple[str, str | None] | None = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self.on_message: Any = None
        self.on_disconnect: Any = None

    def username_pw_set(self, username: str, password: str | None = None) -> None:
        self.credentials = (username, password)

    def connect(self, host: str, port: int) -> None:
        self.connected.append((host, port))

    def subscribe(self, topic: str) -> None:
        self.subscribed.append(topic)

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, payload, qos, retain))

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_stopped = True

    def disconnect(self) -> None:
        self.disconnected = True

    # Test helpers -------------------------------------------------------
    def fire_message(self, payload: bytes | str) -> None:
        self.on_message(self, None, FakeMessage(payload))

    def fire_disconnect(self) -> None:
        self.on_disconnect(self, None, 0)


class FakeTracker:
    def __init__(self) -> None:
        self.pushed: list[tuple[float, float]] = []

    def push(self, rssi: float, now: float) -> None:
        self.pushed.append((rssi, now))


class ScriptedBackend:
    """A PresenceBackend that lets the test set presence directly."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._on_present: Any = None

    async def start(self, on_present: Any) -> None:
        self.started = True
        self._on_present = on_present

    async def stop(self) -> None:
        self.stopped = True

    def emit(self, present: bool | None) -> None:
        self._on_present(present)


class ImmediateSleep:
    """Injectable sleep that yields control once then returns immediately."""

    async def __call__(self, _seconds: float) -> None:
        await asyncio.sleep(0)


# ------------------------------------------------------- parse_present_values


class TestParsePresentValues:
    def test_defaults_on_empty(self) -> None:
        values = parse_present_values("")
        assert "on" in values and "home" in values and "1" in values

    def test_defaults_on_whitespace(self) -> None:
        assert parse_present_values("   ") == parse_present_values("")

    def test_strips_and_lowercases_and_drops_empties(self) -> None:
        values = parse_present_values(" ON ,, Home, ")
        assert values == frozenset({"on", "home"})


# ------------------------------------------------------- MqttPresenceBackend


class TestMqttPresenceBackend:
    def _start(self, present_values: frozenset[str]) -> tuple[FakeMqttClient, list[bool | None]]:
        client = FakeMqttClient()
        seen: list[bool | None] = []
        backend = MqttPresenceBackend(
            "broker",
            1883,
            "home/presence",
            present_values,
            username="user",
            password="pass",
            client_factory=lambda: client,
        )
        asyncio.run(backend.start(seen.append))
        return client, seen

    def test_start_connects_subscribes_and_sets_credentials(self) -> None:
        client, _seen = self._start(frozenset({"on"}))
        assert client.connected == [("broker", 1883)]
        assert client.subscribed == ["home/presence"]
        assert client.credentials == ("user", "pass")
        assert client.loop_started is True

    def test_present_payload_case_insensitive(self) -> None:
        client, seen = self._start(frozenset({"on", "home"}))
        client.fire_message(b"HOME")
        assert seen == [True]

    def test_absent_payload_reports_false(self) -> None:
        client, seen = self._start(frozenset({"on"}))
        client.fire_message("off")
        assert seen == [False]

    def test_unrecognised_payload_reports_none(self) -> None:
        client, seen = self._start(frozenset({"on"}))
        client.fire_message("banana")
        assert seen == [None]

    def test_disconnect_reports_none(self) -> None:
        client, seen = self._start(frozenset({"on"}))
        client.fire_disconnect()
        assert seen == [None]

    def test_stop_is_idempotent_and_silent(self) -> None:
        client = FakeMqttClient()
        backend = MqttPresenceBackend(
            "broker", 1883, "t", frozenset({"on"}), client_factory=lambda: client
        )
        asyncio.run(backend.start(lambda _p: None))
        asyncio.run(backend.stop())
        asyncio.run(backend.stop())  # second stop must not raise
        assert client.loop_stopped is True
        assert client.disconnected is True

    def test_missing_paho_degrades_to_noop(self) -> None:
        # No client_factory and paho import made to fail -> presence unknown.
        backend = MqttPresenceBackend("broker", 1883, "t", frozenset({"on"}))
        import builtins

        real_import = builtins.__import__

        def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
            if name.startswith("paho"):
                raise ImportError("paho absent")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = blocked
        try:
            asyncio.run(backend.start(lambda _p: None))
        finally:
            builtins.__import__ = real_import
        # No client was built; stop stays safe.
        asyncio.run(backend.stop())


# ----------------------------------------------------- ExternalPresenceSource


def _drive_source(source: ExternalPresenceSource, backend: ScriptedBackend, present: bool | None):
    async def run() -> None:
        await source.start()
        await asyncio.sleep(0)  # let the poll task register
        backend.emit(present)
        await asyncio.sleep(0.02)  # let a few poll ticks run
        await source.stop()

    asyncio.run(run())


class TestExternalPresenceSource:
    def test_present_pushes_near_rssi(self) -> None:
        tracker = FakeTracker()
        backend = ScriptedBackend()
        source = ExternalPresenceSource(tracker, backend, poll_interval=0.0, sleep=ImmediateSleep())
        _drive_source(source, backend, True)
        assert tracker.pushed
        assert all(r == PRESENT_RSSI_DBM for r, _ in tracker.pushed)

    def test_absent_pushes_nothing(self) -> None:
        tracker = FakeTracker()
        backend = ScriptedBackend()
        source = ExternalPresenceSource(tracker, backend, poll_interval=0.0, sleep=ImmediateSleep())
        _drive_source(source, backend, False)
        assert tracker.pushed == []

    def test_unknown_pushes_nothing(self) -> None:
        tracker = FakeTracker()
        backend = ScriptedBackend()
        source = ExternalPresenceSource(tracker, backend, poll_interval=0.0, sleep=ImmediateSleep())
        _drive_source(source, backend, None)
        assert tracker.pushed == []

    def test_connection_loss_pushes_nothing(self) -> None:
        # Presence goes True (pushes), then a disconnect emits None -> pushing
        # stops. Fail-safe: connection loss never keeps synthesizing presence.
        tracker = FakeTracker()
        backend = ScriptedBackend()
        source = ExternalPresenceSource(tracker, backend, poll_interval=0.0, sleep=ImmediateSleep())

        async def run() -> int:
            await source.start()
            await asyncio.sleep(0)
            backend.emit(True)
            await asyncio.sleep(0.02)
            count_after_present = len(tracker.pushed)
            backend.emit(None)  # simulate connection lost
            await asyncio.sleep(0.02)
            await source.stop()
            return count_after_present

        count_after_present = asyncio.run(run())
        assert count_after_present > 0  # present phase did push
        # After the disconnect pushing stops; at most one tick was in flight.
        assert len(tracker.pushed) <= count_after_present + 1

    def test_stop_cancels_and_stops_backend(self) -> None:
        tracker = FakeTracker()
        backend = ScriptedBackend()
        source = ExternalPresenceSource(tracker, backend, poll_interval=0.0, sleep=ImmediateSleep())

        async def run() -> None:
            await source.start()
            await source.stop()
            await source.stop()  # idempotent

        asyncio.run(run())
        assert backend.started is True
        assert backend.stopped is True

    def test_retarget_is_noop(self) -> None:
        source = ExternalPresenceSource(FakeTracker(), ScriptedBackend())
        source.retarget("AA:BB:CC:DD:EE:FF")  # must not raise or change behaviour


# ----------------------------------------------------------------- MqttNotifier


class TestMqttNotifier:
    def test_notify_publishes_json_to_topic(self) -> None:
        client = FakeMqttClient()
        notifier = MqttNotifier("broker", 1883, "stavau/events", client_factory=lambda: client)
        notifier.notify("locked", reason="away")
        assert len(client.published) == 1
        topic, payload, qos, retain = client.published[0]
        assert topic == "stavau/events"
        assert qos == 0 and retain is False
        assert json.loads(payload) == {"event": "locked", "reason": "away"}

    def test_notify_connects_once(self) -> None:
        client = FakeMqttClient()
        notifier = MqttNotifier("broker", 1883, "t", client_factory=lambda: client)
        notifier.notify("locked")
        notifier.notify("unlocked")
        assert client.connected == [("broker", 1883)]  # single connect
        assert len(client.published) == 2

    def test_notify_swallows_client_exceptions(self) -> None:
        class RaisingClient(FakeMqttClient):
            def publish(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("broker down")

        notifier = MqttNotifier("broker", 1883, "t", client_factory=lambda: RaisingClient())
        # Must not propagate.
        notifier.notify("locked")

    def test_connect_error_is_swallowed(self) -> None:
        class RaisingConnect(FakeMqttClient):
            def connect(self, host: str, port: int) -> None:
                raise OSError("no route to broker")

        notifier = MqttNotifier("broker", 1883, "t", client_factory=lambda: RaisingConnect())
        notifier.notify("locked")  # must not raise

    def test_close_is_idempotent(self) -> None:
        client = FakeMqttClient()
        notifier = MqttNotifier("broker", 1883, "t", client_factory=lambda: client)
        notifier.notify("locked")
        notifier.close()
        notifier.close()  # idempotent
        assert client.loop_stopped is True
        assert client.disconnected is True


class TestNullNotifier:
    def test_notify_and_close_are_safe_noops(self) -> None:
        notifier = NullNotifier()
        notifier.notify("locked", detail=1)
        notifier.close()


# ------------------------------------------------------------------- factories


class TestFactories:
    def test_make_presence_backend_none_on_empty_host(self) -> None:
        assert make_presence_backend("", 1883, "t", frozenset({"on"}), "", "") is None

    def test_make_presence_backend_none_on_empty_topic(self) -> None:
        assert make_presence_backend("broker", 1883, "", frozenset({"on"}), "", "") is None

    def test_make_presence_backend_builds_when_configured(self) -> None:
        backend = make_presence_backend("broker", 1883, "t", frozenset({"on"}), "user", "pass")
        assert isinstance(backend, MqttPresenceBackend)

    def test_make_notifier_null_on_empty(self) -> None:
        assert isinstance(make_notifier("", 1883, "t", "", ""), NullNotifier)
        assert isinstance(make_notifier("broker", 1883, "", "", ""), NullNotifier)

    def test_make_notifier_builds_when_configured(self) -> None:
        assert isinstance(make_notifier("broker", 1883, "t", "", ""), MqttNotifier)
