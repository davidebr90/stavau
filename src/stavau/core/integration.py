"""Smart-home integration (CARD-M1): MQTT-first, both directions.

Two boundaries let stavau interoperate with Home Assistant (and, through it,
Matter / Z-Wave / Thread / Wi-Fi presence) without embedding any radio stack:

  * CONSUME — an external presence signal published on an MQTT topic becomes a
    ``ProximitySource``. When the source reports "present" we synthesize a near
    RSSI into the tracker; anything else (absent / unknown / connection lost)
    pushes nothing, so the tracker goes stale and the session locks. This is
    the fail-safe invariant I1: an external signal can never *hold* the screen
    unlocked without live evidence, it can only supply evidence while present.

  * EMIT — lock / unlock events are published to MQTT so home-automation
    routines can react (dim lights, arm an alarm, etc.). A broker problem must
    never affect locking, so the notifier swallows every error.

Network invariant I3: this module makes NO network I/O by default. Nothing
connects at import time and nothing connects in a constructor. The paho-mqtt
import is function-local and guarded (mirroring
``core.classic.WinRtConnectionBackend``) so the module imports cleanly and
type-checks on every platform even when paho is absent; a missing paho degrades
to a no-op (presence stays unknown -> fail-safe far).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from stavau.core.classic import PRESENT_RSSI_DBM

if TYPE_CHECKING:
    from stavau.core.monitor import RssiTracker

__all__ = [
    "PRESENT_RSSI_DBM",
    "PresenceBackend",
    "MqttPresenceBackend",
    "ExternalPresenceSource",
    "IntegrationNotifier",
    "MqttNotifier",
    "NullNotifier",
    "make_presence_backend",
    "make_notifier",
    "parse_present_values",
]

_log = logging.getLogger(__name__)

# Payload tokens treated as "present" when none are configured explicitly.
_DEFAULT_PRESENT_VALUES = "on,home,present,occupied,true,1"


def _new_paho_client(missing_message: str) -> Any:
    """Construct a paho-mqtt v2 client, or None if paho is not installed.

    The import is deliberately function-local and guarded (invariant I3: no
    network stack is touched at import time). ``mqtt`` is bound as ``Any`` so
    type-checking passes uniformly whether or not paho is present on the box
    running mypy.
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        _log.warning(missing_message)
        return None
    client: Any = mqtt
    return client.Client(client.CallbackAPIVersion.VERSION2)


# --------------------------------------------------------------- presence (in)


class PresenceBackend(Protocol):
    """Source of an external present/absent/unknown signal.

    ``on_present`` is called with True (present), False (absent) or None
    (unknown / unrecognised payload / connection lost -> fail-safe).
    """

    async def start(self, on_present: Callable[[bool | None], None]) -> None: ...
    async def stop(self) -> None: ...


class MqttPresenceBackend:
    """Presence backend fed by an MQTT topic (e.g. a Home Assistant sensor).

    Inert until ``start()``: paho-mqtt is imported lazily there and, if it is
    missing, the backend logs once and no-ops (presence stays unknown). Each
    retained/live message on ``topic`` is compared (stripped, lower-cased)
    against ``present_values`` -> on_present(True); a recognised "not present"
    style payload -> on_present(False); an unrecognised payload -> on_present(None).
    A disconnect calls on_present(None) so the tracker goes stale (fail-safe).
    """

    # Payloads we affirmatively read as "absent" (as opposed to unrecognised).
    _ABSENT_VALUES = frozenset(
        {"off", "not_home", "away", "absent", "unoccupied", "false", "0", "no"}
    )

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        present_values: frozenset[str],
        username: str = "",
        password: str = "",
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._present_values = present_values
        self._username = username
        self._password = password
        self._client_factory = client_factory
        self._client: Any | None = None
        self._on_present: Callable[[bool | None], None] | None = None

    async def start(self, on_present: Callable[[bool | None], None]) -> None:
        self._on_present = on_present
        client = self._build_client()
        if client is None:
            # paho missing and no injected factory: presence stays unknown.
            return
        self._client = client
        client.on_message = self._handle_message
        client.on_disconnect = self._handle_disconnect
        if self._username:
            client.username_pw_set(self._username, self._password or None)
        client.connect(self._host, self._port)
        client.subscribe(self._topic)
        client.loop_start()

    def _build_client(self) -> Any | None:
        if self._client_factory is not None:
            return self._client_factory()
        return _new_paho_client(
            "paho-mqtt not installed; MQTT presence disabled "
            "(install the 'integration' extra). Presence stays unknown."
        )

    def _handle_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        if self._on_present is None:  # pragma: no cover - start() sets it
            return
        payload = message.payload
        if isinstance(payload, (bytes, bytearray)):
            text = bytes(payload).decode("utf-8", errors="replace")
        else:
            text = str(payload)
        token = text.strip().lower()
        if token in self._present_values:
            self._on_present(True)
        elif token in self._ABSENT_VALUES:
            self._on_present(False)
        else:
            self._on_present(None)

    def _handle_disconnect(self, *_args: Any, **_kwargs: Any) -> None:
        # Never synthesize presence on connection loss (invariant I1).
        if self._on_present is not None:
            self._on_present(None)

    async def stop(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


class ExternalPresenceSource:
    """``ProximitySource`` driven by a ``PresenceBackend``.

    On each poll tick, if the latest presence is True we push ``PRESENT_RSSI_DBM``
    into the tracker (a strong "near" reading); otherwise we push nothing and let
    the tracker's staleness carry the fail-safe (the session locks). This is a
    binary presence channel with no distance information of its own.

    ``retarget`` is a deliberate no-op: an external presence sensor is not bound
    to a Bluetooth address — it reports the presence of *a* trusted person, and
    which BLE device stavau otherwise tracks is irrelevant to it.
    """

    def __init__(
        self,
        tracker: RssiTracker,
        backend: PresenceBackend,
        poll_interval: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._tracker = tracker
        self._backend = backend
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._present: bool | None = None
        self._task: asyncio.Task[None] | None = None

    def retarget(self, address: str) -> None:
        """No-op: external presence is not tied to a Bluetooth address."""

    def _on_present(self, present: bool | None) -> None:
        self._present = present

    async def start(self) -> None:
        await self._backend.start(self._on_present)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._backend.stop()

    async def _poll_loop(self) -> None:
        import time

        while True:
            if self._present is True:
                # Present -> synthesize a near RSSI. Absent/unknown -> push
                # nothing so staleness drives the fail-safe (invariant I1).
                self._tracker.push(PRESENT_RSSI_DBM, time.monotonic())
            await self._sleep(self._poll_interval)


# ------------------------------------------------------------------ action (out)


class IntegrationNotifier(Protocol):
    """Emits lock/unlock events to a home-automation boundary."""

    def notify(self, event: str, **detail: object) -> None: ...
    def close(self) -> None: ...


class MqttNotifier:
    """Publishes lock/unlock events as small JSON payloads to an MQTT topic.

    Lazily connects on first ``notify()`` (or an explicit ``connect()``). Every
    error is swallowed: a broker problem must never propagate into the locking
    path. Inert until the first publish; nothing connects at construction.
    """

    def __init__(
        self,
        host: str,
        port: int,
        topic: str,
        username: str = "",
        password: str = "",
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._topic = topic
        self._username = username
        self._password = password
        self._client_factory = client_factory
        self._client: Any | None = None

    def connect(self) -> None:
        if self._client is not None:
            return
        client = self._build_client()
        if client is None:
            return
        if self._username:
            client.username_pw_set(self._username, self._password or None)
        client.connect(self._host, self._port)
        client.loop_start()
        self._client = client

    def _build_client(self) -> Any | None:
        if self._client_factory is not None:
            return self._client_factory()
        return _new_paho_client(
            "paho-mqtt not installed; MQTT event emission disabled "
            "(install the 'integration' extra)."
        )

    def notify(self, event: str, **detail: object) -> None:
        try:
            self.connect()
            if self._client is None:
                return
            payload = json.dumps({"event": event, **detail})
            self._client.publish(self._topic, payload, qos=0, retain=False)
        except Exception as exc:  # noqa: BLE001 - a broker problem must not affect locking
            _log.warning("MQTT notify failed (%s): %s", event, exc)

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.loop_stop()
        with contextlib.suppress(Exception):
            client.disconnect()


class NullNotifier:
    """No-op notifier so the session can always hold one without branching."""

    def notify(self, event: str, **detail: object) -> None:
        return

    def close(self) -> None:
        return


# --------------------------------------------------------------------- wiring


def parse_present_values(csv: str) -> frozenset[str]:
    """Parse a comma-separated list into a lowercased, stripped value set.

    Empty entries are dropped; an empty/blank input falls back to the defaults.
    """
    source = csv if csv.strip() else _DEFAULT_PRESENT_VALUES
    return frozenset(part.strip().lower() for part in source.split(",") if part.strip())


def make_presence_backend(
    host: str,
    port: int,
    topic: str,
    present_values: frozenset[str],
    username: str,
    password: str,
) -> MqttPresenceBackend | None:
    """Build an MQTT presence backend, or None when host/topic are empty."""
    if not host or not topic:
        return None
    return MqttPresenceBackend(
        host, port, topic, present_values, username=username, password=password
    )


def make_notifier(
    host: str,
    port: int,
    topic: str,
    username: str,
    password: str,
) -> IntegrationNotifier:
    """Build an MQTT notifier, or a NullNotifier when host/topic are empty."""
    if not host or not topic:
        return NullNotifier()
    return MqttNotifier(host, port, topic, username=username, password=password)
