"""Linux lock-state observer via systemd-logind, over the system D-Bus.

Event source: ``org.freedesktop.login1`` on the SYSTEM bus. The session object
path is resolved via ``Manager.GetSessionByPID(os.getpid())``, falling back to
``Manager.GetSession(os.environ["XDG_SESSION_ID"])`` when the PID lookup
fails (e.g. a session manager quirk, or a process that has been re-parented).
Once resolved, the ``org.freedesktop.login1.Session`` interface exposes a
readonly boolean property ``LockedHint`` plus ``Lock``/``Unlock`` signals;
``LockedHint`` changes normally arrive as
``org.freedesktop.DBus.Properties.PropertiesChanged``.

Library: `dbus-fast <https://github.com/Bluetooth-Devices/dbus-fast>`_, an
asyncio-native D-Bus library. It is already a transitive dependency of
``bleak`` on Linux, so nothing new is added to the dependency graph. All
imports of ``dbus_fast`` happen inside functions/methods (never at module
scope) so this module imports cleanly on Windows/macOS, where the package is
not installed, and so ``mypy`` can type-check it uniformly across platforms
(mirrors the guarded-import style used for ``winrt`` in
``stavau.core.classic``).

Concurrency design
-------------------
``LockStateObserver.current()`` is a *synchronous* method, but talking to the
system bus is inherently asynchronous. There is no synchronous "connect on
construction" option that would not block the caller's event loop, and
``make_observer()`` (the platform factory hook) is itself a synchronous
function, so it cannot ``await`` a connect step either.

The chosen design: the observer is constructed *unstarted*. Its cache
(``_state``) starts at ``None`` (unknown — the safe default per invariant
I1). The very first time ``current()`` is called, it tries to obtain the
*running* event loop via ``asyncio.get_running_loop()`` and, if one is
running, schedules its own ``_connect_and_listen()`` coroutine as a
background task on that loop. If no loop is running yet (e.g. ``current()``
is called before the caller's event loop has started), the attempt is
skipped silently and retried on the next call — this never raises and never
blocks. Once the connect task lands, it seeds the cache with a real initial
``Get(LockedHint)`` query and then subscribes to change notifications so the
cache stays fresh without further polling of D-Bus itself (the *caller*, e.g.
``MonitorSession``, still polls ``current()`` once per tick, which is a cheap
attribute read).

This matches how ``MonitorSession`` drives the observer (see
``stavau.core.session``): it runs on a single asyncio event loop and calls
``current()`` once per tick, so scheduling the connect task the first time
``current()`` observes a running loop is exactly the right hook point.

Every failure mode — bus connection errors, timeouts, missing session,
malformed property payloads — degrades the cache to ``None`` ("unknown") and
never propagates out of this module. Per invariant I1, unknown state must
never suppress locking; only an affirmative ``True`` (confirmed locked) does.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any, Protocol

_log = logging.getLogger(__name__)

_BUS_NAME = "org.freedesktop.login1"
_MANAGER_PATH = "/org/freedesktop/login1"
_MANAGER_IFACE = "org.freedesktop.login1.Manager"
_SESSION_IFACE = "org.freedesktop.login1.Session"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"


def _parse_locked_hint(changed_properties: dict[str, Any]) -> bool | None:
    """Pure parser for a PropertiesChanged (or Get) payload's LockedHint entry.

    ``changed_properties`` maps property names to either a raw ``bool`` or a
    dbus Variant-like wrapper (duck-typed via a ``.value`` attribute — this
    lets tests and real ``dbus_fast.Variant`` instances both work without an
    import of dbus_fast here). Returns ``None`` when the key is missing or the
    value cannot be interpreted as a plain bool.
    """
    if "LockedHint" not in changed_properties:
        return None
    raw = changed_properties["LockedHint"]
    value = raw.value if hasattr(raw, "value") else raw
    if isinstance(value, bool):
        return value
    return None


class _SessionProxyLike(Protocol):
    """Shape of the object returned by a session-proxy factory.

    Kept minimal and duck-typed so tests can inject a fake without depending
    on dbus_fast's real proxy/interface classes.
    """

    async def get_locked_hint(self) -> bool | None: ...

    def on_properties_changed(
        self, callback: Callable[[str, dict[str, Any], list[str]], None]
    ) -> None: ...

    def on_lock(self, callback: Callable[[], None]) -> None: ...

    def on_unlock(self, callback: Callable[[], None]) -> None: ...

    async def disconnect(self) -> None: ...


SessionProxyFactory = Callable[[], "_SessionProxyLike"]
"""An async-callable-free factory: calling it returns an *awaitable-capable*
session proxy object (see ``_DbusFastSessionProxy`` below for the real one).
Tests inject their own factory to avoid touching a real bus.
"""


class LinuxLockStateObserver:
    """Lock-state observer for Linux, backed by systemd-logind over D-Bus.

    See the module docstring for the connect-on-first-``current()``-call
    design and the failure-degrades-to-None invariant.
    """

    name = "linux-logind"

    def __init__(self, session_proxy_factory: SessionProxyFactory | None = None) -> None:
        # When None, the real dbus_fast-backed factory is created lazily
        # inside _connect_and_listen so importing this module never requires
        # dbus_fast to be installed.
        self._session_proxy_factory = session_proxy_factory
        self._state: bool | None = None
        self._task: asyncio.Task[None] | None = None
        self._subscribers: list[Callable[[bool], None]] = []
        self._proxy: _SessionProxyLike | None = None
        self._closed = False

    def current(self) -> bool | None:
        """Return the cached lock state, lazily scheduling the connect task.

        Never blocks and never raises: if no event loop is currently running,
        the connect attempt is simply skipped and retried on the next call.
        """
        if not self._closed and self._task is None:
            with contextlib.suppress(RuntimeError):
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._connect_and_listen())
        return self._state

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        self._subscribers.append(cb)

    def close(self) -> None:
        """Cancel the background task and disconnect. Idempotent, never raises."""
        self._closed = True
        # Drop subscribers first: a signal that arrives during/after teardown
        # must never invoke a stale callback on a closed observer.
        self._subscribers.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        proxy = self._proxy
        self._proxy = None
        if proxy is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_disconnect_quietly(proxy))
        else:
            # No running loop to schedule the async disconnect on (shutdown
            # ordering, a signal handler, or a test): run it to completion
            # best-effort so the system-bus connection is not leaked.
            with contextlib.suppress(Exception):
                asyncio.run(_disconnect_quietly(proxy))

    async def _connect_and_listen(self) -> None:
        try:
            factory = self._session_proxy_factory or _default_session_proxy_factory
            proxy = await _call_factory(factory)
            initial = await proxy.get_locked_hint()
        except Exception as exc:
            # Any bus error, timeout, or malformed reply degrades to unknown
            # and must never propagate (invariant I1). Log once (this task runs
            # a single time) so a permanently-unknown observer is diagnosable
            # rather than indistinguishable from a working, idle one.
            _log.debug("lock-state observer unavailable, staying 'unknown': %s", exc)
            self._state = None
            return
        self._proxy = proxy
        self._set_state(initial)

        def on_props_changed(_iface: str, changed: dict[str, Any], _invalidated: list[str]) -> None:
            parsed = _parse_locked_hint(changed)
            if parsed is not None:
                self._set_state(parsed)

        def on_lock() -> None:
            self._set_state(True)

        def on_unlock() -> None:
            self._set_state(False)

        with contextlib.suppress(Exception):
            proxy.on_properties_changed(on_props_changed)
            proxy.on_lock(on_lock)
            proxy.on_unlock(on_unlock)

    def _set_state(self, value: bool | None) -> None:
        if self._closed:
            # A late signal must not mutate state or notify after close.
            return
        self._state = value
        if value is None:
            return
        for cb in list(self._subscribers):
            with contextlib.suppress(Exception):
                cb(value)


def _session_id_from_env(environ: Mapping[str, str]) -> str:
    """Read XDG_SESSION_ID, raising a clear error when it is absent.

    Using ``environ["XDG_SESSION_ID"]`` directly raises a bare ``KeyError`` in a
    headless/re-parented session (no ``PAMName``), which is opaque; this raises a
    descriptive ``RuntimeError`` instead. The caller degrades it to unknown
    (invariant I1), and it is logged once in ``_connect_and_listen``.
    """
    session_id = environ.get("XDG_SESSION_ID")
    if not session_id:
        raise RuntimeError(
            "cannot resolve the logind session: GetSessionByPID failed and "
            "XDG_SESSION_ID is unset (headless or re-parented process?)"
        )
    return session_id


async def _call_factory(factory: Callable[[], Any]) -> _SessionProxyLike:
    """Call a session-proxy factory, awaiting the result if it is awaitable.

    Real factories are async (they must connect to the bus); fakes used in
    tests may be plain sync callables. Supporting both keeps test doubles
    simple. Typed loosely (``Callable[[], Any]``) because the real factory is
    an async def (returns a coroutine) while ``SessionProxyFactory`` describes
    the eventual awaited result, not the callable's own return type.
    """
    result = factory()
    if asyncio.iscoroutine(result):
        return await result  # type: ignore[no-any-return]
    return result  # type: ignore[no-any-return]


async def _disconnect_quietly(proxy: _SessionProxyLike) -> None:
    with contextlib.suppress(Exception):
        await proxy.disconnect()


# ------------------------------------------------------------- real backend


class _DbusFastSessionProxy:
    """Real session-proxy backed by dbus_fast, used outside of tests.

    All dbus_fast imports live inside this class's methods so the module
    stays importable (and mypy-checkable) on non-Linux platforms.
    """

    def __init__(self) -> None:
        self._bus: Any = None
        self._session_iface: Any = None
        self._props_iface: Any = None

    @classmethod
    async def connect(cls) -> _DbusFastSessionProxy:
        # dbus_fast is Linux-only (a transitive bleak dependency) and ships no
        # stubs; covered by the mypy override in pyproject.
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        self = cls()
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        session_path = await self._resolve_session_path()
        introspection = await self._bus.introspect(_BUS_NAME, session_path)
        proxy_object = self._bus.get_proxy_object(_BUS_NAME, session_path, introspection)
        self._session_iface = proxy_object.get_interface(_SESSION_IFACE)
        self._props_iface = proxy_object.get_interface(_PROPS_IFACE)
        return self

    async def _resolve_session_path(self) -> str:
        manager_introspection = await self._bus.introspect(_BUS_NAME, _MANAGER_PATH)
        manager_object = self._bus.get_proxy_object(_BUS_NAME, _MANAGER_PATH, manager_introspection)
        manager_iface = manager_object.get_interface(_MANAGER_IFACE)
        try:
            path: str = await manager_iface.call_get_session_by_pid(os.getpid())
            return path
        except Exception:
            session_id = _session_id_from_env(os.environ)
            path = await manager_iface.call_get_session(session_id)
            return path

    async def get_locked_hint(self) -> bool | None:
        variant: Any = await self._props_iface.call_get(_SESSION_IFACE, "LockedHint")
        return _parse_locked_hint({"LockedHint": variant})

    def on_properties_changed(
        self, callback: Callable[[str, dict[str, Any], list[str]], None]
    ) -> None:
        self._props_iface.on_properties_changed(callback)

    def on_lock(self, callback: Callable[[], None]) -> None:
        self._session_iface.on_lock(callback)

    def on_unlock(self, callback: Callable[[], None]) -> None:
        self._session_iface.on_unlock(callback)

    async def disconnect(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()


async def _default_session_proxy_factory() -> _SessionProxyLike:
    return await _DbusFastSessionProxy.connect()


def make_observer() -> LinuxLockStateObserver | None:
    """Return a fresh, unstarted observer on Linux, else None."""
    if not sys.platform.startswith("linux"):
        return None
    return LinuxLockStateObserver()
