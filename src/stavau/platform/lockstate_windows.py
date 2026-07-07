"""Windows lock-state observer via WTS session-change notifications.

Event source: a message-only window receives ``WM_WTSSESSION_CHANGE``
(0x02B1) once registered for terminal-services session notifications via
``WTSRegisterSessionNotification``. ``wParam`` carries ``WTS_SESSION_LOCK``
(0x7) on lock and ``WTS_SESSION_UNLOCK`` (0x8) on unlock; other codes (logon,
logoff, remote-control, and so on) are ignored here.

The observer is edge-triggered only: there is no reliable "is the session
locked right now" query, so ``current()`` starts at ``None`` (unknown) and
only becomes a definite ``bool`` after the first transition notification
arrives. Reporting unknown is always safe per invariant I1 (see
``stavau.platform.lockstate``): an unknown state never suppresses locking, it
just forgoes the "already locked" optimization until the first event.

All Windows-specific setup is guarded behind ``sys.platform == "win32"`` and
performed lazily inside ``_start()``/``make_observer()`` so importing this
module and constructing the observer for tests never touches ctypes/windll
and never creates a real window, matching the pattern in
``stavau.platform.windows``.

Threading model: a dedicated daemon thread owns the message-only window and
runs the ``GetMessageW``/``DispatchMessageW`` loop. The WNDPROC ctypes
callback is kept alive as an instance attribute (``self._wndproc``) because a
garbage-collected callback trampoline crashes the process when Windows calls
into freed memory. The WNDPROC delegates to ``_handle_session_change``, a
pure method that flips the cached state under a lock and invokes subscribed
callbacks with exceptions suppressed, so a broken callback can never wedge
the message loop or corrupt the cached state.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable

# Terminal-services session-change notification and lock/unlock reasons.
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8

# Window-message plumbing needed to run a message-only window and pump it.
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
HWND_MESSAGE = -3
NOTIFY_FOR_THIS_SESSION = 0
CW_USEDEFAULT = 0x80000000


def _window_class_name(token: int) -> str:
    """Per-instance window-class name.

    ``RegisterClassW`` fails with ``ERROR_CLASS_ALREADY_EXISTS`` on a duplicate
    class name; a second observer sharing one fixed name would then run against
    the first's registration and cross-wire session callbacks. Deriving the name
    from a per-instance token (``id(self)``) keeps every observer's class unique.
    """
    return f"StavauLockStateWindow_{token:x}"


class WindowsLockStateObserver:
    """Lock-state observer backed by WTS session notifications.

    Constructing this object never touches ctypes or creates a window: that
    plumbing only runs when :meth:`_start` is invoked, which
    :func:`make_observer` does for real use. Tests exercise
    ``_handle_session_change`` and the public protocol methods directly
    without ever calling ``_start``, keeping them platform-independent.
    """

    name = "windows-wts"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: bool | None = None
        self._callbacks: list[Callable[[bool], None]] = []
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hwnd: int | None = None
        # Kept alive so the ctypes callback trampoline is never freed while
        # Windows can still call into it.
        self._wndproc: Callable[[int, int, int, int], int] | None = None
        self._started = False
        self._closed = False

    # -- Public LockStateObserver protocol -------------------------------

    def current(self) -> bool | None:
        with self._lock:
            return self._state

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def close(self) -> None:
        """Tear down the notification thread and window. Idempotent, never raises."""
        if self._closed:
            return
        self._closed = True
        thread = self._thread
        thread_id = self._thread_id
        if thread is not None and thread.is_alive() and thread_id is not None:
            if sys.platform == "win32":  # narrow for mypy: windll is Windows-only
                with contextlib.suppress(Exception):
                    import ctypes

                    ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=2.0)

    # -- Pure state-transition logic (unit-testable, no Windows needed) --

    def _handle_session_change(self, wparam: int) -> None:
        """Update cached state for a lock/unlock notification and notify subscribers.

        Any other wparam value is ignored. Callback exceptions are
        suppressed so one broken subscriber cannot crash the message loop or
        prevent other subscribers from being notified.
        """
        if wparam == WTS_SESSION_LOCK:
            new_state = True
        elif wparam == WTS_SESSION_UNLOCK:
            new_state = False
        else:
            return

        with self._lock:
            self._state = new_state
            callbacks = list(self._callbacks)

        for cb in callbacks:
            with contextlib.suppress(Exception):
                cb(new_state)

    # -- Windows-only setup, never exercised by tests --------------------

    def _start(self) -> bool:
        """Create the message-only window and notification thread.

        Returns True on success. Any failure (old Windows, no session,
        unavailable API) is swallowed and returns False so the caller
        (``make_observer``) can degrade to "no observer" rather than raise —
        observing the lock state is an optional enhancement.
        """
        if sys.platform != "win32":  # pragma: no cover — factory prevents this
            return False
        if self._started:
            return True

        ready = threading.Event()
        ok = threading.Event()

        thread = threading.Thread(
            target=self._run_message_loop,
            args=(ready, ok),
            name="stavau-wts-lockstate",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        ready.wait(timeout=5.0)
        if not ok.is_set():
            self._thread = None
            return False
        self._started = True
        return True

    def _run_message_loop(self, ready: threading.Event, ok: threading.Event) -> None:
        if sys.platform != "win32":  # pragma: no cover — _start prevents this
            ready.set()  # ok is never set: _start reports failure
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            wtsapi32 = ctypes.windll.wtsapi32
            kernel32 = ctypes.windll.kernel32

            # Explicit prototypes: without them ctypes marshals handles and
            # LRESULT through 32-bit c_int, which truncates 64-bit HWNDs and
            # mangles HWND_MESSAGE (-3) — CreateWindowExW then fails or crashes.
            LRESULT = ctypes.c_ssize_t
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            user32.DefWindowProcW.restype = LRESULT
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                wintypes.LPVOID,
            ]
            user32.DestroyWindow.argtypes = [wintypes.HWND]
            user32.GetMessageW.restype = ctypes.c_int
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = LRESULT
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
            wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            # NB: the Win32 export really is spelled with a capital R: WTSUnRegister...
            wtsapi32.WTSUnRegisterSessionNotification.restype = wintypes.BOOL
            wtsapi32.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]

            self._thread_id = kernel32.GetCurrentThreadId()

            WNDPROC = ctypes.WINFUNCTYPE(
                LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )

            def _wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
                if msg == WM_WTSSESSION_CHANGE:
                    self._handle_session_change(wparam)
                    return 0
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                result = user32.DefWindowProcW(hwnd, msg, wparam, lparam)
                return int(result)

            self._wndproc = WNDPROC(_wndproc)

            class WNDCLASS(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            user32.RegisterClassW.restype = wintypes.ATOM

            class_name = _window_class_name(id(self))
            hinstance = kernel32.GetModuleHandleW(None)

            wndclass = WNDCLASS()
            wndclass.style = 0
            wndclass.lpfnWndProc = self._wndproc
            wndclass.cbClsExtra = 0
            wndclass.cbWndExtra = 0
            wndclass.hInstance = hinstance
            wndclass.hIcon = None
            wndclass.hCursor = None
            wndclass.hbrBackground = None
            wndclass.lpszMenuName = None
            wndclass.lpszClassName = class_name

            if not user32.RegisterClassW(ctypes.byref(wndclass)):
                # A per-instance class name means this should not collide, but a
                # zero ATOM is a real registration failure — fail cleanly.
                ok.clear()
                ready.set()
                return

            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "StavauLockStateWindow",
                0,
                0,
                0,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                HWND_MESSAGE,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                ok.clear()
                ready.set()
                return
            self._hwnd = hwnd

            if not wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
                user32.DestroyWindow(hwnd)
                self._hwnd = None
                ok.clear()
                ready.set()
                return

            ok.set()
            ready.set()

            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            with contextlib.suppress(Exception):
                wtsapi32.WTSUnRegisterSessionNotification(hwnd)
            with contextlib.suppress(Exception):
                user32.DestroyWindow(hwnd)
            self._hwnd = None
        except Exception:
            # A failure after the window/registration were created must not leak
            # them: a live WTS registration against a destroyed HWND can misroute
            # later notifications. Clean up whatever exists (guarded — the names
            # only exist if we got that far).
            with contextlib.suppress(Exception):
                if self._hwnd:
                    wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)
                    user32.DestroyWindow(self._hwnd)
            self._hwnd = None
            ok.clear()
            ready.set()


def make_observer() -> WindowsLockStateObserver | None:
    """Build and start a :class:`WindowsLockStateObserver`, or return None on failure.

    Used by the factory wiring in ``stavau.platform.lockstate``. Construction
    is failure-safe: any problem starting the notification thread/window
    (old Windows, no interactive session, API unavailable) degrades to "no
    observer" rather than raising, matching the contract of
    ``get_lock_state_observer``.
    """
    if sys.platform != "win32":
        return None
    observer = WindowsLockStateObserver()
    if not observer._start():
        return None
    return observer
