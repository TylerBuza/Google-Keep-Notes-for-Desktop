"""System-tray icon and main application controller."""

import logging
import os
import time
import uuid
import threading
from queue import Empty, SimpleQueue
from functools import partial

from PySide6.QtCore import Qt, QTimer, Slot, Signal, QObject, QUrl
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QAction, QFont
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QFrame, QWidget, QCheckBox, QListWidget, QTextEdit,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile
from login_security import (GuardedGooglePage, AuthRequestInterceptor, display_address,
                            allowed_https_url, release_auth_profile)
from keep_protocol.auth import AUTH_CONSENT_VERSION
from keep_protocol.auth_transport import auth_failure_message, safe_auth_failure, valid_account_email

from config import (
    load_config, save_config, set_autostart, is_autostart_enabled, save_token,
    SYNC_INTERVAL_MS, DATA_DIR, load_token,
    load_json, save_json, APP_NAME, APP_VERSION, GITHUB_REPO,
    load_notes, save_notes, AUTO_UPDATE_ENABLED,
)
from app_icon import make_icon as _make_icon
import note_window
from note_window import NoteWindow
from note_model import KeepNote
from note_manager import NoteManagerDialog, edit_snapshot
from updater import UpdateChecker, prompt_and_install

log = logging.getLogger(__name__)

VISIBILITY_FILE = os.path.join(DATA_DIR, "visibility.json")

# How many periodic sync ticks between full resyncs. A full resync is
# the only thing that reliably surfaces a formatting-only web edit (see
# _periodic_sync), so run it often enough to feel responsive while any
# note is on screen, and rarely when nothing is.
_FULL_RESYNC_TICKS_ACTIVE = 4      # ~2 minutes at the 30s interval
_FULL_RESYNC_TICKS_IDLE = 20       # ~10 minutes


def _is_local_note_id(note_id):
    try:
        uuid.UUID(note_id)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _set_window_app_id(window, app_id: str) -> None:
    """Set per-window AppUserModelID via IPropertyStore so Windows can
    group (or refuse to group) this HWND in the taskbar.

    No-op on non-Windows. Failures raise OSError so the caller can log
    the HRESULT.

    IMPORTANT: changing the AppUserModelID of a window that is already
    visible has NO effect on its existing taskbar button. The window
    must be hidden, the property changed, and the window shown again.
    """
    import sys
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    hwnd = int(window.winId())

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]

    # PROPVARIANT is 24 bytes on x64. We only need the LPWSTR variant;
    # use a c_void_p for the union slot so InitPropVariantFromString /
    # PropVariantClear can manage the buffer via CoTaskMemAlloc/Free.
    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("data1", ctypes.c_void_p),   # union slot 1 (pwszVal etc.)
            ("data2", ctypes.c_void_p),   # union slot 2 (padding)
        ]

    IID_IPropertyStore = GUID(
        0x886D8EEB, 0x8CF2, 0x4446,
        (ctypes.c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99),
    )
    PKEY_AppUserModel_ID = PROPERTYKEY(
        GUID(0x9F4C2855, 0x9F79, 0x4B39,
             (ctypes.c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)),
        5,
    )

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    # Build a VT_LPWSTR PROPVARIANT manually instead of relying on
    # propsys!InitPropVariantFromString — that export is missing on
    # some Windows builds / ctypes views (we hit "function not found"
    # in the wild). The shape is well-defined: vt=31 (VT_LPWSTR) and
    # the union slot holds a pointer to a wide string allocated with
    # CoTaskMemAlloc. PropVariantClear will CoTaskMemFree it for us.
    VT_LPWSTR = 31
    CoTaskMemAlloc = ole32.CoTaskMemAlloc
    CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
    CoTaskMemAlloc.restype = ctypes.c_void_p

    PropVariantClear = ole32.PropVariantClear
    PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
    PropVariantClear.restype = ctypes.c_long

    SHGetPropertyStoreForWindow = shell32.SHGetPropertyStoreForWindow
    SHGetPropertyStoreForWindow.argtypes = [
        wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    SHGetPropertyStoreForWindow.restype = ctypes.c_long

    pStore = ctypes.c_void_p()
    hr = SHGetPropertyStoreForWindow(
        hwnd, ctypes.byref(IID_IPropertyStore), ctypes.byref(pStore)
    )
    if hr != 0 or not pStore.value:
        raise OSError(
            f"SHGetPropertyStoreForWindow hwnd=0x{hwnd:X} "
            f"hr=0x{hr & 0xFFFFFFFF:08X}"
        )

    # IPropertyStore vtable: 0=QueryInterface 1=AddRef 2=Release
    # 3=GetCount 4=GetAt 5=GetValue 6=SetValue 7=Commit
    vtbl_ptr = ctypes.cast(pStore, ctypes.POINTER(ctypes.c_void_p))[0]
    vtbl = ctypes.cast(vtbl_ptr, ctypes.POINTER(ctypes.c_void_p))
    SetValue = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p,
        ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT),
    )(vtbl[6])
    Commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])

    pv = PROPVARIANT()
    try:
        # Allocate a CoTaskMem-owned wide-char copy of app_id (with NUL),
        # then populate the PROPVARIANT manually.
        nbytes = (len(app_id) + 1) * ctypes.sizeof(ctypes.c_wchar)
        buf = CoTaskMemAlloc(nbytes)
        if not buf:
            raise OSError("CoTaskMemAlloc failed")
        ctypes.memmove(
            buf,
            ctypes.create_unicode_buffer(app_id),
            nbytes,
        )
        pv.vt = VT_LPWSTR
        pv.data1 = buf
        hr = SetValue(pStore, ctypes.byref(PKEY_AppUserModel_ID),
                      ctypes.byref(pv))
        if hr != 0:
            raise OSError(
                f"IPropertyStore::SetValue hr=0x{hr & 0xFFFFFFFF:08X}"
            )
        hr = Commit(pStore)
        if hr != 0:
            raise OSError(
                f"IPropertyStore::Commit hr=0x{hr & 0xFFFFFFFF:08X}"
            )
        log.debug("AppUserModelID set on hwnd=0x%X -> %r", hwnd, app_id)
    finally:
        PropVariantClear(ctypes.byref(pv))
        Release(pStore)


def _force_foreground(window) -> None:
    """Force ``window`` into the Windows foreground.

    SetForegroundWindow is normally blocked when our process doesn't
    own the active foreground (which is the case when called from a
    tray-icon callback). We combine three workarounds, each of which
    independently bypasses the restriction in different scenarios:

      1. Synthesise an ALT keystroke. Windows treats any pending input
         from our thread as an "active" signal and lifts the lock.
      2. AttachThreadInput to the foreground thread's input queue, so
         our SetForegroundWindow looks like it came from that thread.
      3. SystemParametersInfo SPI_SETFOREGROUNDLOCKTIMEOUT = 0 around
         the call, then restore the previous timeout.

    No-op on non-Windows or if the underlying APIs fail.
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(window.winId())

        # Workaround 1: ALT keystroke — unblocks SetForegroundWindow.
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        # Workaround 3: relax the foreground-lock timeout so other
        # processes can't race us. We don't bother restoring it —
        # leaving it at 0 only affects our own process and Windows
        # resets it on the next foreground change anyway.
        SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
        SPIF_SENDCHANGE = 0x0002
        user32.SystemParametersInfoW(
            SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, SPIF_SENDCHANGE,
        )

        # Workaround 2: attach input queues, then promote the window.
        fg = user32.GetForegroundWindow()
        our_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != our_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, our_tid, True))
        try:
            # If minimised, restore first — SetForegroundWindow on a
            # minimised window leaves it minimised.
            SW_RESTORE = 9
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
            user32.SetActiveWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, our_tid, False)
    except Exception:  # noqa: BLE001
        pass


def _load_visibility() -> dict:
    return load_json(VISIBILITY_FILE, {})


def _save_visibility(vis: dict):
    save_json(VISIBILITY_FILE, vis)


def _escape_html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Step granularity for new sort values — matches Keep web's typical
# spacing. Big enough that the midpoint between two adjacent values
# always has room for further inserts without collision.
_REORDER_STEP = 1 << 22  # ~4M


def _compute_new_sort_value(ordered: list, note) -> int:
    """Pick a new ``sortValue`` for ``note`` so Keep web mirrors the
    desktop order in ``ordered`` (left-to-right == top-to-bottom).

    The bug we're guarding against here: ``ordered`` is keyed by
    ``local_order``, which can disagree with each note's ``sort_key``
    after a push failure or sync race. So the immediate visual
    neighbour in ``ordered`` is NOT always the right anchor — for
    edge moves (top / bottom) we need to dominate / be dominated by
    every other note's sort_key, not just the neighbour. Otherwise
    Keep web only sees a one-step move when the user asked for
    "move to top" or "move to bottom".

    Pure function for testability; no side effects.
    """
    def _sv(n):
        try:
            return int(getattr(n, "sort_key", 0) or 0)
        except (TypeError, ValueError):
            return 0

    try:
        new_idx = ordered.index(note)
    except ValueError:
        return _sv(note) or _REORDER_STEP

    above = ordered[new_idx - 1] if new_idx > 0 else None
    below = ordered[new_idx + 1] if new_idx < len(ordered) - 1 else None
    other_svs = [_sv(n) for n in ordered if n is not note]

    if above is None and below is not None:
        # "Move to top" — must beat every other sort_key on the wire,
        # not just the visual neighbour.
        return (max(other_svs) if other_svs else 0) + _REORDER_STEP
    if below is None and above is not None:
        # "Move to bottom" — symmetric.
        return (min(other_svs) if other_svs else 0) - _REORDER_STEP
    if above is not None and below is not None:
        a, b = _sv(above), _sv(below)
        if a == b:
            return a - 1  # tiebreak
        new_sv = (a + b) // 2
        if new_sv == a or new_sv == b:
            return a - 1
        return new_sv
    # Single-note list: keep whatever sort_key it had.
    return _sv(note) or _REORDER_STEP


# ═══════════════════════════════════════════════════════════════════════
#  Login Dialog – Embedded browser Google sign-in
# ═══════════════════════════════════════════════════════════════════════

EMBEDDED_SETUP_URL = "https://accounts.google.com/EmbeddedSetup"


class _LoginSignals(QObject):
    """Qt cookie notification; authentication results use a Python queue."""
    token_found = Signal(str)        # oauth_token cookie value


class LoginDialog(QDialog):
    """Opens an embedded Google sign-in browser.

    After the user authenticates, the oauth_token cookie is captured
    and exchanged through the endpoint-locked Google transport.
    """

    def __init__(self, first_launch=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KeepDesktop – Sign in to Google")
        self.setMinimumWidth(560)
        self.signed_in = False
        self._keep_sync = None
        self._captured_token = None
        self._cancelled = threading.Event()
        self._login_started = False
        self._remember_login = False
        self._offline_requested = False
        self._signin_requested = False
        self._token_cookie_received = False
        self._exchange_attempted = False
        self._page_loaded = None
        self._navigation_blocked = False
        self._background_requests_blocked = False
        self._auth_failure = None
        self._page_stalled = False
        self._exchange_timed_out = False
        self._exchange_timer = QTimer(self)
        self._exchange_timer.setSingleShot(True)
        self._exchange_timer.setInterval(90000)
        self._exchange_timer.timeout.connect(self._on_exchange_timeout)
        self._page_load_timer = QTimer(self)
        self._page_load_timer.setSingleShot(True)
        self._page_load_timer.setInterval(45000)
        self._page_load_timer.timeout.connect(self._on_page_load_stalled)
        self._login_results = SimpleQueue()
        self._worker_result_timer = QTimer(self)
        self._worker_result_timer.setInterval(50)
        self._worker_result_timer.timeout.connect(self._consume_login_result)
        self._signals = _LoginSignals(self)
        self._signals.token_found.connect(self._on_token_found)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # No network sign-in page loads before explicit informed consent.
        self._risk_consent = QCheckBox("Remember on this PC (powerful master token, encrypted)")
        layout.addWidget(self._risk_consent)
        self._details_btn = QPushButton("Security details")
        self._details_btn.setCheckable(True)
        layout.addWidget(self._details_btn)
        self._retention_details = QLabel(
            "Sign-in saves a powerful Google master token, not just Keep-only permission. "
            "Keep access renews across restarts until sign-out or Google's rejection/revocation. "
            "There is no fixed app time limit or guaranteed number of days.\n\n"
            "Windows encryption protects the token on disk. Other software running as your "
            "Windows user can access it. "
            "Signing out removes this app's saved credential but does not revoke it at Google. "
            "Revoke the device/session in Google Account Security if needed.\n\n"
            "Only allowlisted Google-owned HTTPS destinations are permitted by the app. "
            "Third-party resources and opening external links are blocked. Cloud writes start OFF. "
            "You can cancel and use offline notes."
        )
        self._retention_details.setTextFormat(Qt.TextFormat.PlainText)
        self._retention_details.setWordWrap(True)
        self._retention_details.setStyleSheet("padding: 8px; font-size: 12px;")
        self._retention_details.hide()
        self._details_btn.toggled.connect(self._toggle_security_details)
        layout.addWidget(self._retention_details)
        self._continue_btn = QPushButton("Continue to Google")
        self._continue_btn.setDefault(True)
        self._details_btn.setAutoDefault(False)
        self._continue_btn.setEnabled(False)
        self._risk_consent.toggled.connect(self._update_continue_enabled)
        self._continue_btn.clicked.connect(self._begin_signin)
        layout.addWidget(self._continue_btn)

        self._origin = QLineEdit("Not connected — target: " + EMBEDDED_SETUP_URL)
        self._origin.setReadOnly(True)
        self._origin.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._origin.setCursorPosition(0)
        self._origin.setToolTip("Actual Google HTTPS origin and path. Secret query/fragment values are hidden.")
        self._origin.setAccessibleName("Google sign-in origin and path (queries hidden)")
        layout.addWidget(self._origin)

        # Status label (hidden until needed)
        self.status_label = QLabel("")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("padding: 8px; font-size: 12px;")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        # Embedded browser
        # An unnamed profile is off-the-record; Google cookies stay in memory.
        self._profile = QWebEngineProfile(QApplication.instance())
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self._profile.setPersistentPermissionsPolicy(QWebEngineProfile.PersistentPermissionsPolicy.StoreInMemory)
        self._profile.setSpellCheckEnabled(False)
        self._profile.downloadRequested.connect(lambda download: download.cancel())
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie)
        self._interceptor = AuthRequestInterceptor(self._profile)
        self._profile.setUrlRequestInterceptor(self._interceptor)
        self._interceptor.blocked.connect(self._on_login_blocked)
        self._interceptor.background_blocked.connect(self._on_background_request_blocked)

        self._browser = QWebEngineView()
        self._browser.setMinimumHeight(360)
        self._browser.hide()
        new_page = GuardedGooglePage(self._profile, self._browser)
        # A profile must outlive the *complete* page destructor. Delete it on
        # the next event-loop turn after the page, not as a child of the page.
        new_page.destroyed.connect(partial(release_auth_profile, self._profile))
        new_page.blocked.connect(self._on_login_blocked)
        self._browser.setPage(new_page)
        self._browser.urlChanged.connect(self._on_url_changed)
        self._browser.loadStarted.connect(self._on_page_load_started)
        self._browser.loadFinished.connect(self._on_page_loaded)
        layout.addWidget(self._browser, stretch=1)

        # Bottom button row
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 6, 8, 6)
        if first_launch:
            skip_btn = QPushButton("Use offline")
            skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            skip_btn.setToolTip("Skip sign-in on future starts. You can sign in from the tray any time.")
            skip_btn.clicked.connect(self._use_offline)
            btn_row.addWidget(skip_btn)
        else:
            cancel_btn = QPushButton("Cancel")
            cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            cancel_btn.clicked.connect(self.reject)
            btn_row.addWidget(cancel_btn)
        btn_row.addStretch()

        layout.addLayout(btn_row)
        self.finished.connect(self._cleanup_signin)
        self.adjustSize()

    def set_keep_sync(self, sync):
        self._keep_sync = sync

    @Slot()
    def _use_offline(self):
        self._offline_requested = True
        self.reject()

    @Slot(bool)
    def _toggle_security_details(self, visible):
        self._retention_details.setVisible(visible)
        if not self._signin_requested:
            self.adjustSize()

    @Slot()
    def _update_continue_enabled(self):
        self._continue_btn.setEnabled(self._risk_consent.isChecked()
            and not self._signin_requested)

    def _begin_signin(self):
        if not self._risk_consent.isChecked() or self._signin_requested:
            return
        self._remember_login = True
        self._login_started = True
        self._signin_requested = True
        self._risk_consent.setEnabled(False)
        self._continue_btn.hide()
        self._details_btn.setChecked(False)
        self._browser.show()
        available = self.screen().availableGeometry().height() - 60
        self.resize(self.width(), min(820, available))
        self._save_login_diagnostics()
        self._browser.setUrl(QUrl(EMBEDDED_SETUP_URL))
        self._browser.setFocus()

    @Slot(str)
    def _on_login_blocked(self, message):
        self._navigation_blocked = True
        self._show_status(message, error=True)
        self._save_login_diagnostics()

    @Slot()
    def _on_background_request_blocked(self):
        if not self._background_requests_blocked:
            self._background_requests_blocked = True
            self._retention_details.setText(self._retention_details.text() +
                "\n\nSome background requests were blocked by the Google-only policy. "
                "This does not by itself mean sign-in failed.")
        self._save_login_diagnostics()

    def _save_login_diagnostics(self):
        if not self._signin_requested:
            return
        # Deliberately exclude full URLs, unknown hostnames, cookies, identity,
        # passwords, token values, note contents and note counts.
        report = {
            "schema": 1,
            "google_page_loaded": self._page_loaded,
            "page_blocked": self._navigation_blocked,
            "token_cookie_received": self._token_cookie_received,
            "token_exchange_attempted": self._exchange_attempted,
            "login_succeeded": self.signed_in,
            "page_load_stalled": self._page_stalled,
            "exchange_timed_out": self._exchange_timed_out,
            "allowed_resource_hosts_observed": self._interceptor.observed_hosts(),
            "blocked_requests": self._interceptor.blocked_requests(),
            "auth_failure": safe_auth_failure(self._auth_failure),
        }
        try:
            save_json(os.path.join(DATA_DIR, "login-validation.json"), report)
        except Exception:
            log.warning("Could not save non-secret sign-in diagnostics")

    @Slot(QUrl)
    def _on_url_changed(self, url):
        self._origin.setText(display_address(url))
        self._origin.setCursorPosition(0)  # Never scroll the trusted origin out of view.

    @Slot()
    def _on_page_load_started(self):
        if self._login_started and not self._exchange_attempted:
            self._page_load_timer.start()

    @Slot()
    def _on_page_load_stalled(self):
        if self._login_started and not self._exchange_attempted:
            self._page_stalled = True
            self._show_status("Google's page is taking longer than expected. You can cancel and try again.")
            self._save_login_diagnostics()

    @Slot(bool)
    def _on_page_loaded(self, success):
        self._page_load_timer.stop()
        if self._captured_token or not self._login_started:
            return
        self._page_loaded = bool(success and allowed_https_url(self._browser.url()))
        self._save_login_diagnostics()
        if self._navigation_blocked:
            return
        if self._page_loaded:
            self.status_label.hide()
        else:
            self._show_status("Google sign-in could not load securely. Do not bypass a warning; "
                              "Google may block this experimental embedded flow.", error=True)

    def _cleanup_signin(self, result):
        self._exchange_timer.stop()
        self._worker_result_timer.stop()
        self._page_load_timer.stop()
        self._save_login_diagnostics()
        self._login_started = False
        accepted = result == QDialog.DialogCode.Accepted and self.signed_in
        if not accepted:
            self._cancelled.set()
        self._browser.setEnabled(True)
        self._browser.stop()
        self._browser.hide()
        page = self._browser.page()
        page.setVisible(False)
        page.setLifecycleState(page.LifecycleState.Discarded)
        # Keep filtering requests until the page is actually destroyed.
        # release_auth_profile then detaches the IO hook and deletes profile.
        self._profile.cookieStore().deleteAllCookies()
        self._captured_token = None
        if not accepted:
            if self._keep_sync and self._keep_sync.is_authenticated:
                try:
                    self._keep_sync.logout()
                except Exception:
                    pass  # logout disables in-memory access even on storage failure.

    @Slot(QNetworkCookie)
    def _on_cookie(self, cookie):
        """Called for every cookie set by the embedded browser."""
        if not self._login_started or self._cancelled.is_set():
            return
        name = bytes(cookie.name()).decode("utf-8", errors="replace")
        domain = cookie.domain().lstrip(".").lower()
        if (name == "oauth_token" and domain in ("accounts.google.com", "google.com")
                and cookie.isSecure() and not self._captured_token):
            value = bytes(cookie.value()).decode("utf-8", errors="replace")
            if value.startswith("oauth2_4/") and len(value) <= 16384:
                self._token_cookie_received = True
                self._captured_token = value
                self._signals.token_found.emit(value)

    @Slot(str)
    def _on_token_found(self, oauth_token):
        """OAuth cookie captured — exchange it for a master token."""
        if self._cancelled.is_set():
            return
        self._exchange_attempted = True
        self._save_login_diagnostics()
        self._page_load_timer.stop()
        # Let Google's terms/consent navigation finish. Aborting the browser
        # from the cookie hand-off can interrupt that same Google navigation.
        # The destination policy remains active until normal page teardown.
        self._browser.setEnabled(False)
        self._show_status("Finishing sign-in…")
        self._exchange_timer.start()
        self._worker_result_timer.start()
        threading.Thread(
            target=self._exchange_worker, args=(oauth_token,), daemon=True
        ).start()

    @Slot()
    def _on_exchange_timeout(self):
        if self._exchange_attempted and not self.signed_in:
            self._worker_result_timer.stop()
            self._exchange_timed_out = True
            self._cancelled.set()
            self._show_status("Sign-in timed out. Cancel this window, then try again.", error=True)
            self._save_login_diagnostics()

    def _queue_login_result(self, success, message):
        # This method may run in a Python worker. Do not emit a Qt signal or
        # access a widget from that thread, including after dialog cancellation.
        if not self._cancelled.is_set():
            self._login_results.put((success, message))

    @Slot()
    def _consume_login_result(self):
        try:
            result = self._login_results.get_nowait()
        except Empty:
            return
        self._on_login_result(*result)

    def _exchange_worker(self, oauth_token):
        """Runs in a background thread: exchange oauth_token → master_token → login."""
        if self._cancelled.is_set() or not self._keep_sync:
            self._queue_login_result(False, "Internal error: no sync object.")
            return

        resp = self._keep_sync.exchange_oauth_for_master(oauth_token)
        if not resp:
            self._auth_failure = getattr(self._keep_sync, "last_auth_failure", None)
            self._queue_login_result(
                False, auth_failure_message(self._auth_failure)
            )
            return

        master_token = resp.get("Token")
        # Only Google's authenticated exchange can supply account identity.
        # Never scrape its form or guess from local data or an editable URL.
        resolved_email = resp.get("Email")
        if not valid_account_email(resolved_email):
            self._queue_login_result(False, "Google did not return a verified account identity.")
            return

        # Now authenticate through the hardened Keep protocol client.
        try:
            ok = self._keep_sync.login(resolved_email, master_token=master_token,
                                       remember=self._remember_login, consent=True,
                                       android_id=resp.get("AndroidId"), cancel_event=self._cancelled)
        except Exception:
            ok = False
        if self._cancelled.is_set():
            if ok:
                self._keep_sync.logout()
            return
        if ok:
            self._queue_login_result(True, resolved_email)
        else:
            self._auth_failure = getattr(self._keep_sync, "last_auth_failure", None)
            self._queue_login_result(False, auth_failure_message(self._auth_failure))

    @Slot(bool, str)
    def _on_login_result(self, success, message):
        self._exchange_timer.stop()
        self._worker_result_timer.stop()
        self._login_started = False
        self._captured_token = None
        self._browser.setEnabled(True)
        # Do not clear Google's cookies while its terms/navigation is still
        # live. The discarded, off-record page is cleared on dialog teardown.
        if self._cancelled.is_set():
            return
        if success:
            self.signed_in = True
            self._email = message
            self._show_status(f"Signed in as {message}", error=False)
            QTimer.singleShot(600, self.accept)
        else:
            self._show_status(message, error=True)
        self._save_login_diagnostics()

    def _show_status(self, text, error=False):
        color = "#c62828" if error else "#1A73E8"
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; padding: 8px;"
        )
        self.status_label.setText(text)
        self.status_label.show()

    def keyPressEvent(self, event):
        # Prevent Enter/Return from activating dialog buttons — let the browser handle it
        if self._login_started and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._browser.setFocus()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self._focus_signin_control)

    def _focus_signin_control(self):
        if self._login_started:
            self._browser.setFocus()
        else:
            self._risk_consent.setFocus()

    def get_email(self):
        return getattr(self, "_email", "")


# ═══════════════════════════════════════════════════════════════════════
#  App Controller
# ═══════════════════════════════════════════════════════════════════════

class AppController(QObject):
    """Orchestrates note windows, tray icon, and Keep sync."""

    _remote_notes_ready = Signal(list)
    _note_merged_during_push = Signal(str)  # note_id
    _sync_notice = Signal(str)
    _sync_conflict = Signal(str)
    _delete_finished = Signal(str, bool)
    _save_requested = Signal()
    _read_sync_failed = Signal()

    def __init__(self):
        super().__init__()
        self.config = load_config()
        # Apply the saved text-size preference before any NoteWindow is
        # built so freshly-opened notes render at the right scale from
        # the start (not just ones open when the setting later changes).
        note_window.set_font_scale(self.config.get("font_scale", 1.0))
        # Reconcile the autostart flag with reality. The Inno Setup
        # installer creates a Startup-folder shortcut directly without
        # touching our config, so a fresh install would otherwise show
        # the in-app toggle as OFF even though Windows IS configured to
        # launch us at login. Source of truth is the .lnk on disk.
        real_autostart = is_autostart_enabled()
        if self.config.get("autostart", False) != real_autostart:
            log.info(
                "autostart: reconciling config (%s) with disk state (%s)",
                self.config.get("autostart", False), real_autostart,
            )
            self.config["autostart"] = real_autostart
            save_config(self.config)
        # Never silently fall back to the unsafe legacy writer.
        from hardened_sync import HardenedKeepSync
        self.sync = HardenedKeepSync(
            writes_enabled=self.config.get("cloud_writes_enabled", False),
            on_notice=self._sync_notice.emit, on_conflict=self._sync_conflict.emit,
        )
        self._sync_notice.connect(self._on_sync_problem)
        self._sync_conflict.connect(self._on_sync_conflict)
        self._delete_finished.connect(self._finish_delete)
        self._save_requested.connect(self._save_notes_to_disk)
        self._read_sync_failed.connect(self._on_read_sync_failed)
        self._read_sync_error_active = False
        self._show_manager_after_sync = False
        self.windows: dict[str, NoteWindow] = {}
        self._notes: dict[str, KeepNote] = {}
        self._dirty: set[str] = set()
        # Notes whose CACHE (self._notes[id].text/.html/.styled_doc)
        # was just updated -- by push_note's merge result, or by a
        # remote pull the user was too busy to have applied to their
        # widget immediately -- but whose WIDGET hasn't been confirmed
        # refreshed to match yet (the refresh is deferred: queued via
        # a cross-thread signal for the push case, or retried on a
        # timer until idle for the busy-pull case). Included in
        # hold_baseline_for alongside self._dirty and busy_ids so
        # KeepSyncV2's fetch_notes() doesn't advance _base_text/
        # _base_doc to the server's latest before the widget has
        # actually caught up -- a note in this gap is neither dirty
        # nor busy, so without tracking it explicitly here, the very
        # next periodic sync cycle's fetch would treat the widget's
        # (still-stale) content as if it were the confirmed baseline,
        # and the next local edit would silently revert the
        # just-merged/just-pulled content right back off the server.
        self._pending_widget_refresh: set[str] = set()
        # Per-note record of the StyledDoc actually rendered into the
        # window. decide_merge needs a local structured view to spot a
        # formatting-only remote change (plain text can't reveal one),
        # and note.styled_doc can't serve: _on_note_changed clears it on
        # every body edit, so between a local edit and the next pull it
        # is simply absent -- and a formatting-only web change landing
        # in that gap got written into the cache but never rendered,
        # after which no later pull ever saw a difference to refresh
        # for. What the window is SHOWING is the honest thing to
        # compare against, and it survives local edits.
        self._rendered_doc: dict = {}
        # Periodic-sync counter driving the adaptive full-resync
        # cadence in _periodic_sync.
        self._tick_count = 0
        # Re-entrancy guard shared between _push_dirty_notes (the
        # debounced post-edit push) and _sync_worker's own push phase
        # -- deliberately separate from _sync_running, which guards
        # _full_sync's entire push+pull cycle. See _push_dirty_notes'
        # own comment for why conflating the two starves the periodic
        # pull (the only path that brings in web edits) during active
        # typing, when the debounced push fires far more often than
        # the 30s periodic tick.
        self._push_running = False
        # Notes the user just deleted locally for which the threaded
        # `sync.delete_note` trash POST hasn't yet completed. We must
        # ignore these in `_apply_remote_notes`, otherwise a periodic
        # sync that races ahead of the trash request will see the note
        # still untrashed on the server and re-add it locally — which
        # then reappears in the manager after relaunch.
        self._pending_deletes: set[str] = set()
        # Notes that have gone missing from a single sync response.
        # We require them to be missing for ``STRIKES_TO_DELETE``
        # consecutive syncs before treating it as a real remote
        # deletion — otherwise a transient sync that returns fewer
        # notes than usual (server hiccup, push response that only
        # echoes the changed node, etc.) wipes notes that quietly
        # come back next cycle, producing visible churn in the
        # manager dialog. Maps note_id -> consecutive miss count.
        self._missing_strikes: dict[str, int] = {}
        # Per-note timestamp of the last user edit. Used to decide
        # whether a focused note window is "actively being edited" or
        # just sitting open — if it's been idle long enough we let
        # remote refreshes through so web-side changes appear without
        # the user having to close and reopen the note.
        self._last_edit_time: dict[str, float] = {}
        # How long after the last keystroke we still treat a note as
        # "being edited" and refuse to overwrite it from a remote pull.
        self._edit_idle_seconds = 15.0
        self._visibility = _load_visibility()
        self._show_in_taskbar = self.config.get("show_in_taskbar", False)

        # Marshal remote-note results from worker thread back to GUI thread
        self._remote_notes_ready.connect(self._apply_remote_notes)
        self._note_merged_during_push.connect(self._refresh_window_when_idle)

        # Load persisted notes from disk
        self._load_notes_from_disk()

        self.tray = QSystemTrayIcon(_make_icon())
        self.tray.setToolTip("KeepDesktop")
        self.tray.activated.connect(self._on_tray_activated)
        self._build_tray_menu()
        self.tray.show()

        # Debounce timer for saving notes to disk
        self._save_debounce = QTimer()
        self._save_debounce.setSingleShot(True)
        self._save_debounce.setInterval(1000)
        self._save_debounce.timeout.connect(self._save_notes_to_disk)

        # Debounce timer for pushing edits to Keep
        self._sync_debounce = QTimer()
        self._sync_debounce.setSingleShot(True)
        self._sync_debounce.setInterval(5000)
        self._sync_debounce.timeout.connect(self._push_dirty_notes)

        self._sync_timer = QTimer()
        self._sync_timer.timeout.connect(self._periodic_sync)

        # Show persisted notes first
        self._show_persisted_notes()

        self._start_initial_session()

        # Background update check shortly after startup. Silent on failure
        # / when up-to-date; only prompts the user if a newer version is
        # genuinely available.
        self._updater = UpdateChecker()
        self._updater.update_available.connect(self._on_update_available)
        if AUTO_UPDATE_ENABLED:
            QTimer.singleShot(8000, self._updater.check_async)
        self._manual_update_in_progress = False

    def _start_initial_session(self):
        # A dismissed or failed prompt is not completed onboarding. Ignore the
        # legacy seen_login flag; only an explicit offline choice opts out.
        if self.config.get("start_offline") is True:
            if not self.windows and not self.sync.is_authenticated:
                self._new_note()
            return
        email = self.config.get("email", "")
        if (email and self.config.get("remember_google_account") is True
                and type(self.config.get("experimental_auth_consent")) is int
                and self.config["experimental_auth_consent"] == AUTH_CONSENT_VERSION):
            self._do_login(email)
        if not self.sync.is_authenticated:
            QTimer.singleShot(300, self._first_launch_login)

    def _build_tray_menu(self):
        menu = QMenu()

        new_action = QAction("✚  New note", menu)
        new_action.triggered.connect(self._new_note)
        menu.addAction(new_action)

        manage_action = QAction("📋  Manage notes…", menu)
        manage_action.triggered.connect(self._show_note_manager)
        menu.addAction(manage_action)

        show_action = QAction("👁  Bring notes to front", menu)
        show_action.triggered.connect(self._bring_to_front)
        menu.addAction(show_action)

        menu.addSeparator()

        sync_action = QAction("🔄  Sync now", menu)
        sync_action.triggered.connect(self._manual_sync)
        menu.addAction(sync_action)

        self._writes_action = QAction("Two-way Google Keep sync (text, pins, checklists)", menu)
        self._writes_action.setCheckable(True)
        self._writes_action.setChecked(self.sync.writes_enabled)
        self._writes_action.toggled.connect(self._toggle_cloud_writes)
        menu.addAction(self._writes_action)
        conflicts = menu.addAction("Resolve sync conflicts…")
        conflicts.triggered.connect(self._resolve_sync_conflicts)
        recovery = menu.addAction("View saved recovery copies…")
        recovery.triggered.connect(self._show_recovery_copies)

        self._login_action = QAction("", menu)
        self._login_action.triggered.connect(self._on_login_action)
        menu.addAction(self._login_action)
        self._update_login_action_text()

        menu.addSeparator()

        self._taskbar_action = QAction("Show notes in taskbar", menu)
        self._taskbar_action.setCheckable(True)
        self._taskbar_action.setChecked(self._show_in_taskbar)
        self._taskbar_action.toggled.connect(self._toggle_taskbar)
        menu.addAction(self._taskbar_action)

        # Sub-option: only meaningful when notes are in the taskbar.
        # Default ON (group all notes together under one icon). Turning
        # it off gives each note its own taskbar entry.
        self._group_action = QAction("    \u2937 Group notes in taskbar", menu)
        self._group_action.setCheckable(True)
        self._group_action.setChecked(self.config.get("group_in_taskbar", True))
        self._group_action.setEnabled(self._show_in_taskbar)
        self._group_action.toggled.connect(self._toggle_taskbar_grouping)
        menu.addAction(self._group_action)

        autostart_action = QAction("Start with Windows", menu)
        autostart_action.setCheckable(True)
        # Read the live disk state so the menu always reflects whether
        # the Startup-folder shortcut actually exists, even if the
        # installer or another app touched it behind our back.
        autostart_action.setChecked(is_autostart_enabled())
        autostart_action.toggled.connect(self._toggle_autostart)
        menu.addAction(autostart_action)

        menu.addSeparator()

        about_action = QAction("ℹ  About KeepDesktop", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)

        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)

    def _update_login_action_text(self):
        if self.sync.is_authenticated:
            email = self.config.get("email", "")
            self._login_action.setText(f"🔑  Sign out ({email})")
        else:
            self._login_action.setText("🔑  Sign in to Google Keep")
        self._update_dashboard_sync_state()

    def _update_dashboard_sync_state(self):
        dlg = getattr(self, "_manager_dlg", None)
        if dlg is not None:
            dlg.set_sync_state(self.sync.is_authenticated,
                               self.config.get("cloud_writes_enabled", False))

    def _on_login_action(self):
        if self.sync.is_authenticated:
            self._show_sign_out()
        else:
            self._show_login()

    def _toggle_cloud_writes(self, enabled):
        if enabled:
            warning = ("A previous write was interrupted or unverified. Check Keep on the web first, "
                       "especially for a note that may already have been created. "
                       if self.sync.pause_file.exists() else "")
            answer = QMessageBox.question(
                None, "Enable cloud writes?",
                warning + "This app uses Google's unsupported Keep protocol. Export a Google Takeout "
                "backup first. Conflicts will pause rather than overwrite either version. "
                "Enable creating, editing and trashing notes on Google Keep?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            enabled = answer == QMessageBox.StandardButton.Yes
        self.sync.set_writes_enabled(enabled)
        self.config["cloud_writes_enabled"] = enabled
        if enabled:
            from hardened_sync import WRITE_CONSENT_VERSION
            self.config["cloud_writes_consent_version"] = WRITE_CONSENT_VERSION
        save_config(self.config)
        self._writes_action.blockSignals(True)
        self._writes_action.setChecked(enabled)
        self._writes_action.blockSignals(False)
        self._update_dashboard_sync_state()
        if enabled:
            self._full_sync(force_resync=True)

    @Slot(str)
    def _on_sync_problem(self, message):
        self.config["cloud_writes_enabled"] = False
        self.sync.set_writes_enabled(False)
        save_config(self.config)
        self._writes_action.blockSignals(True)
        self._writes_action.setChecked(False)
        self._writes_action.blockSignals(False)
        self._update_login_action_text()
        QMessageBox.warning(None, "KeepDesktop — sync paused", message)

    @Slot()
    def _on_read_sync_failed(self):
        # A failed read is not a successful empty notebook. Pause writes and
        # report it once, without a modal dialog every periodic retry.
        self.config["cloud_writes_enabled"] = False
        self.sync.set_writes_enabled(False)
        save_config(self.config)
        self._writes_action.blockSignals(True)
        self._writes_action.setChecked(False)
        self._writes_action.blockSignals(False)
        self._update_login_action_text()
        self.tray.setToolTip("KeepDesktop — Keep notes could not be loaded")
        if not self._read_sync_error_active:
            self._read_sync_error_active = True
            self.tray.showMessage("KeepDesktop — notes not loaded",
                "Could not load Keep notes. Local notes are unchanged; use Sync now to retry.",
                QSystemTrayIcon.MessageIcon.Warning, 8000)

    @Slot(str)
    def _on_sync_conflict(self, note_id):
        self.tray.showMessage("KeepDesktop — sync conflict",
                              "Both versions were saved securely. Use the tray menu → "
                              "Resolve sync conflicts. Nothing was overwritten.",
                              QSystemTrayIcon.MessageIcon.Warning, 10000)

    def _resolve_sync_conflicts(self):
        if not self.sync.conflicts:
            QMessageBox.information(None, "KeepDesktop", "No unresolved sync conflicts.")
            return
        for note_id in list(self.sync.conflicts):
            note = self._notes.get(note_id)
            if note is None:
                continue
            message = QMessageBox()
            message.setWindowTitle("Choose a version — KeepDesktop")
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setText(f"Conflicting edits: {note.title or 'Untitled note'}")
            message.setInformativeText("Both versions are in View saved recovery copies. "
                                       "Choose which version to use, or decide later. "
                                       "Desktop changes will still require cloud writes to be enabled.")
            local = message.addButton("Keep desktop version", QMessageBox.ButtonRole.AcceptRole)
            remote = message.addButton("Keep Google version", QMessageBox.ButtonRole.ActionRole)
            later = message.addButton("Decide later", QMessageBox.ButtonRole.RejectRole)
            message.setDefaultButton(later)
            message.exec()
            choice = message.clickedButton()
            if choice not in (local, remote):
                continue
            try:
                chosen = self.sync.resolve_conflict(note_id,
                                                    "local" if choice is local else "remote",
                                                    current_note=note)
            except Exception:
                QMessageBox.warning(None, "KeepDesktop", "The original note is unavailable or recovery "
                                    "could not be saved. View saved recovery copies to recover the text.")
                continue
            if chosen is not None:
                chosen.local_order, chosen.dark_mode = note.local_order, note.dark_mode
                self._notes[note_id] = chosen
                self._dirty.discard(note_id)
                self._refresh_window(note_id)
            else:
                self._dirty.add(note_id)
            self._save_notes_to_disk()
        self._refresh_manager_if_open()

    def _show_recovery_copies(self):
        import json
        from secure_storage import load_protected
        paths = sorted(self.sync.recovery_dir.glob("*.dat"),
                       key=lambda path: path.stat().st_mtime, reverse=True)[:200]
        dlg = QDialog()
        dlg.setWindowTitle("Saved recovery copies — KeepDesktop")
        dlg.resize(700, 560)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Encrypted on disk for your Windows account. "
                                "Select a copy to view/copy its original text and formatting."))
        listing = QListWidget()
        for path in paths:
            listing.addItem(path.stem[:20])
        layout.addWidget(listing)
        preview = QTextEdit()
        preview.setReadOnly(True)
        layout.addWidget(preview, stretch=1)
        def show_copy(index):
            if index < 0:
                return
            try:
                preview.setPlainText(json.dumps(load_protected(paths[index]), indent=2,
                                                ensure_ascii=False))
            except Exception:
                preview.setPlainText("This recovery copy could not be unlocked. File was not modified.")
        listing.currentRowChanged.connect(show_copy)
        if paths:
            listing.setCurrentRow(0)
        else:
            preview.setPlainText("No recovery copies yet. Copies are saved before cloud writes and on conflicts.")
        close = QPushButton("Close")
        close.clicked.connect(dlg.accept)
        layout.addWidget(close)
        dlg.exec()

    # ── Note persistence ───────────────────────────────────────────────

    def _save_notes_to_disk(self):
        from config import NOTES_FILE
        from keep_protocol.nested_model import styled_doc_to_dict
        data = {}
        for nid, note in list(self._notes.items()):
            entry = {
                "id": note.id,
                "title": note.title,
                "text": note.text,
                "html": note.html,
                "color_hex": note.color_hex,
                "pinned": note.pinned,
                "sort_key": note.sort_key,
                "is_list": note.is_list,
                "list_items": note.list_items,
                "local_order": note.local_order,
                "dark_mode": note.dark_mode,
                "pending_sync": nid in self._dirty,
                "sync_baseline": getattr(self.sync, "baselines", {}).get(nid),
            }
            # Persisting styled_doc means the very first sync after
            # launch has a real baseline to structurally compare
            # against, instead of "no styled_doc yet" being
            # indistinguishable from "we just pushed our own edit" --
            # see decide_merge's local_doc-is-None handling in
            # sync_merge.py, and styled_doc_to_dict's own docstring.
            sdoc = getattr(note, "styled_doc", None)
            if sdoc is not None:
                entry["styled_doc"] = styled_doc_to_dict(sdoc)
            data[nid] = entry
        save_notes(data)

    def _load_notes_from_disk(self):
        from config import NOTES_FILE
        from keep_protocol.nested_model import styled_doc_from_dict
        data = load_notes()
        log.info("Loading %d notes from disk cache (%s)",
                 len(data), NOTES_FILE)
        for nid, d in data.items():
            note = KeepNote(
                id=d["id"],
                title=d.get("title", ""),
                text=d.get("text", ""),
                html=d.get("html", ""),
                color_hex=d.get("color_hex", "#FFF475"),
                pinned=d.get("pinned", False),
                sort_key=d.get("sort_key", 0),
                is_list=d.get("is_list", False),
                list_items=d.get("list_items", []),
                local_order=d.get("local_order", 0),
                dark_mode=d.get("dark_mode", False),
            )
            sdoc = styled_doc_from_dict(d.get("styled_doc"))
            if sdoc is not None:
                note.styled_doc = sdoc  # type: ignore[attr-defined]
            self._notes[nid] = note
            if d.get("pending_sync"):
                self._dirty.add(nid)
            if hasattr(self.sync, "register_cached_note"):
                self.sync.register_cached_note(note, d.get("sync_baseline"))
            if nid not in self._visibility:
                self._visibility[nid] = True

    def _show_persisted_notes(self):
        for note_id, note in self._notes.items():
            if self._visibility.get(note_id, True):
                win = self._create_window(note)
                self.windows[note_id] = win
                # Show first so Qt has a chance to attach window icon
                # to the HWND. Then _apply_taskbar_grouping below will
                # hide/show again to register the taskbar button under
                # the right AppUserModelID — and the icon comes along
                # for the ride. Doing the AppID set BEFORE first show
                # left the taskbar button with no icon.
                win.show()
        # Now that all initial windows have HWNDs and icons, apply the
        # grouping. This hides + reshows each window which is the only
        # supported way to change a window's taskbar AppID at runtime.
        QTimer.singleShot(0, self._apply_taskbar_grouping)

    # ── Note management ────────────────────────────────────────────────

    def _create_window(self, note: KeepNote) -> NoteWindow:
        from config import get_position
        pos = get_position(note.id) or {}
        was_pinned = bool(pos.get("pinned", False))
        win = NoteWindow(
            note_id=note.id,
            title=note.title,
            text=note.text,
            html=note.html,
            color_hex=note.color_hex,
            pinned=was_pinned,
            show_in_taskbar=self._show_in_taskbar,
            list_items=note.list_items if note.is_list else None,
            dark_mode=note.dark_mode,
        )
        # If the v2 fetch attached a fresh StyledDoc, render via the
        # cursor API for faithful empty-line preservation.
        sdoc = getattr(note, "styled_doc", None)
        if sdoc is not None and not note.is_list:
            win._syncing = True
            try:
                win.text_edit.set_styled_doc(sdoc)
            finally:
                # Deferred clear — the highlighter's late textChanged
                # must not read as a user edit (see end_sync_render).
                win.end_sync_render()
        win.note_changed.connect(self._on_note_changed)
        win.note_hidden.connect(self._on_note_hidden)
        win.note_deleted.connect(self._on_note_deleted)
        return win

    def _new_note(self):
        note_id = str(uuid.uuid4())
        note = KeepNote(id=note_id, color_hex="#FFF475", dark_mode=True)
        self._notes[note_id] = note
        self._dirty.add(note_id)
        self._visibility[note_id] = True
        _save_visibility(self._visibility)

        win = self._create_window(note)
        self.windows[note_id] = win
        win.show()
        QTimer.singleShot(0, lambda: self._regroup_window(note_id))

        self._save_notes_to_disk()
        if self.sync.is_authenticated:
            self._sync_debounce.start()

    def _push_new_note(self, note: KeepNote):
        from copy import deepcopy
        submitted = deepcopy(note)
        result = self.sync.create_note(submitted.title, submitted.text, submitted.color_hex,
                                       is_list=submitted.is_list, list_items=submitted.list_items)
        if result:
            old_id = note.id
            note.id = result.id
            self._notes.pop(old_id, None)
            self._notes[result.id] = note
            self._dirty.discard(old_id)
            self._dirty.add(result.id)  # Includes typing during creation.
            was_vis = self._visibility.pop(old_id, True)
            self._visibility[result.id] = was_vis
            _save_visibility(self._visibility)
            # Migrate position data
            from config import load_positions, save_positions
            positions = load_positions()
            if old_id in positions:
                positions[result.id] = positions.pop(old_id)
                save_positions(positions)
            if old_id in self.windows:
                win = self.windows.pop(old_id)
                win.note_id = result.id
                self.windows[result.id] = win
            # Save the UUID -> server-id mapping before allowing another
            # create. A crash before this checkpoint leaves writes paused.
            self._save_notes_to_disk()
            self.sync.acknowledge_created_note(result.id)
            self._save_requested.emit()

    @Slot(str)
    def _on_note_changed(self, note_id):
        win = self.windows.get(note_id)
        note = self._notes.get(note_id)
        if win and note:
            text_before, html_before = note.text, note.html
            note.text = win.get_text()
            note.title = win.get_title()
            note.color_hex = win.color_hex
            note.dark_mode = bool(getattr(win, "dark_mode", False))
            note.html = win.get_html()
            # A local edit to the BODY invalidates any cached server-
            # decoded StyledDoc — drop it so a refresh doesn't
            # overwrite the user's in-flight typing with stale render.
            # Gated on the body actually changing (not e.g. a title-
            # only or colour-only edit, which also routes through
            # this same handler): styled_doc absence is also how
            # sync_merge.py's decide_merge tells "local just echoed
            # its own push back" apart from "a genuine concurrent web
            # restyle arrived" (see its local_doc-is-None branch) --
            # clearing it for edits that never touched the body made
            # that heuristic wrong far more often than intended,
            # silently swallowing a concurrent web restyle whenever it
            # landed in the same cycle as an unrelated title/colour
            # edit.
            if (note.text != text_before or note.html != html_before) and hasattr(note, "styled_doc"):
                try:
                    delattr(note, "styled_doc")
                except AttributeError:
                    pass
            # The window may have been toggled in/out of checklist mode
            # by the user; pick that up so it persists.
            note.is_list = bool(getattr(win, "_is_list", False))
            if note.is_list:
                note.list_items = win.get_list_items()
            else:
                note.list_items = []
            self._dirty.add(note_id)
            self._last_edit_time[note_id] = time.monotonic()
            # Debounce: save to disk in 1s, push to Keep in 5s
            self._save_debounce.start()
            if self.sync.is_authenticated:
                self._sync_debounce.start()
            self._refresh_manager_if_open()

    @Slot(str)
    def _on_note_hidden(self, note_id):
        win = self.windows.get(note_id)
        if win:
            win.save_geometry()
        self._visibility[note_id] = False
        _save_visibility(self._visibility)
        # If the manager is open, reflect this in its checkbox
        dlg = getattr(self, "_manager_dlg", None)
        if dlg is not None:
            dlg.set_checkbox(note_id, False)

    @Slot(str)
    def _on_note_deleted(self, note_id):
        if note_id in self._pending_deletes:
            return
        note = self._notes.get(note_id)
        if note:
            from hardened_sync import snapshot
            try:
                self.sync._save_recovery(local=snapshot(note), reason="delete-request")
            except Exception:
                QMessageBox.warning(None, "KeepDesktop", "Recovery could not be saved; note was not deleted.")
                return
        if self.sync.is_authenticated and not _is_local_note_id(note_id):
            if not self.sync.writes_enabled:
                QMessageBox.information(None, "KeepDesktop", "Cloud writes are disabled. "
                                        "Hide the note instead, or enable writes before deleting it.")
                return
            self._pending_deletes.add(note_id)
            threading.Thread(target=self._delete_worker, args=(note_id,), daemon=True).start()
            return
        self._finish_delete(note_id, True)

    @Slot(str, bool)
    def _finish_delete(self, note_id, success):
        self._pending_deletes.discard(note_id)
        if not success:
            return
        win = self.windows.pop(note_id, None)
        note = self._notes.pop(note_id, None)
        self._rendered_doc.pop(note_id, None)
        self._visibility.pop(note_id, None)
        self._dirty.discard(note_id)
        _save_visibility(self._visibility)
        self._save_notes_to_disk()
        if win:
            win.close()
            win.deleteLater()
        self._refresh_manager_if_open()

    def _delete_worker(self, note_id: str):
        success = False
        try:
            success = self.sync.delete_note(note_id) is True
        except Exception:  # noqa: BLE001
            log.exception("delete_note crashed for %s", note_id[:8])
        finally:
            # Clear the guard regardless — if the trash POST genuinely
            # failed the user can re-delete; we don't want to wedge the
            # note in a never-syncs state.
            self._delete_finished.emit(note_id, success)

    def _bring_to_front(self):
        for nid, win in self.windows.items():
            if self._visibility.get(nid, True) and win.isVisible():
                win.raise_()
                win.activateWindow()

    @Slot(str)
    def _toggle_note_checklist(self, note_id: str):
        """Convert the note between plain text and checklist mode."""
        if not _is_local_note_id(note_id):
            QMessageBox.information(None, "KeepDesktop", "For safety, change existing note types "
                                    "in the official Keep app. New local notes can still be checklists.")
            return
        note = self._notes.get(note_id)
        if note is None:
            return
        win = self.windows.get(note_id)
        if note.is_list:
            # Checklist -> plain text. Strip the prefixes.
            lines = []
            for item in note.list_items or []:
                lines.append(item.get("text", ""))
            note.is_list = False
            note.list_items = []
            note.text = "\n".join(lines)
            note.html = ""  # rich-text from checklist render is no longer valid
        else:
            # Plain text -> checklist.
            raw = note.text or ""
            items = [
                {"text": ln.strip(), "checked": False}
                for ln in raw.splitlines() if ln.strip()
            ]
            if not items:
                items = [{"text": "", "checked": False}]
            note.is_list = True
            note.list_items = items
            note.html = ""
        self._dirty.add(note_id)
        self._last_edit_time[note_id] = time.monotonic()
        if win is not None:
            self._refresh_window(note_id)
        self._save_notes_to_disk()
        self._refresh_manager_if_open()
        if self.sync.is_authenticated:
            self._sync_debounce.start()

    def _toggle_note_pin(self, note_id: str):
        """Toggle the Keep ``isPinned`` flag and sync."""
        note = self._notes.get(note_id)
        if note is None:
            return
        note.pinned = not bool(getattr(note, "pinned", False))
        self._dirty.add(note_id)
        self._last_edit_time[note_id] = time.monotonic()
        self._save_notes_to_disk()
        self._refresh_manager_if_open()
        manager = getattr(self, "_manager_dlg", None)
        if manager is not None and not self.sync.writes_enabled:
            manager.show_pin_notice("Keep pin saved locally — cloud writes are off. "
                                    "Enable writes in the tray to upload; monitor pins are desktop-only.")
        if self.sync.is_authenticated:
            self._sync_debounce.start()

    @Slot(str, str)
    def _reorder_note(self, note_id: str, action: str):
        """Move ``note_id`` within the user-imposed order."""
        note = self._notes.get(note_id)
        if note is None:
            return
        # Build the current ordered list (mirrors NoteManagerDialog sort).
        ordered = sorted(
            self._notes.values(),
            key=lambda n: (
                not n.pinned,
                (0, n.local_order) if n.local_order
                else (1, -int(n.sort_key or 0)),
            ),
        )
        try:
            idx = ordered.index(note)
        except ValueError:
            return
        if action == "top":
            ordered.insert(0, ordered.pop(idx))
        elif action == "bottom":
            ordered.append(ordered.pop(idx))
        elif action == "up" and idx > 0:
            ordered[idx - 1], ordered[idx] = ordered[idx], ordered[idx - 1]
        elif action == "down" and idx < len(ordered) - 1:
            ordered[idx + 1], ordered[idx] = ordered[idx], ordered[idx + 1]
        else:
            return
        # Rewrite local_order densely so the new arrangement sticks.
        # Spacing of 10 leaves room for incremental tweaks later.
        for i, n in enumerate(ordered):
            n.local_order = (i + 1) * 10
        new_sv = _compute_new_sort_value(ordered, note)
        note.sort_key = new_sv
        self._dirty.add(note_id)
        self._last_edit_time[note_id] = time.monotonic()
        self._save_notes_to_disk()
        self._refresh_manager_if_open()
        if self.sync.is_authenticated:
            self._sync_debounce.start()

    # ── Note Manager ───────────────────────────────────────────────────

    def _show_note_manager(self):
        # If already open, just bring it forward instead of opening a duplicate
        existing = getattr(self, "_manager_dlg", None)
        if existing is not None:
            try:
                existing.refresh(self._notes, self._visibility)
                if existing.isMinimized():
                    existing.setWindowState(
                        existing.windowState() & ~Qt.WindowState.WindowMinimized
                        | Qt.WindowState.WindowActive
                    )
                if not existing.isVisible():
                    existing.show()
                existing.raise_()
                existing.activateWindow()
                _force_foreground(existing)
                # Sometimes Windows promotes a different window between
                # our show() and SetForegroundWindow(); a deferred retry
                # after the next event-loop tick catches that race.
                QTimer.singleShot(50, lambda: _force_foreground(existing))
                return
            except RuntimeError:
                # Dialog was deleted from under us — fall through to recreate.
                self._manager_dlg = None
        dlg = NoteManagerDialog(
            self._notes, self._visibility,
            font_scale=self.config.get("font_scale", 1.0),
        )
        dlg.setModal(False)
        dlg.note_deleted.connect(self._on_note_deleted)
        dlg.note_create_requested.connect(lambda: self._open_dashboard_editor())
        dlg.checklist_create_requested.connect(lambda: self._open_dashboard_editor(is_list=True))
        dlg.note_edit_requested.connect(self._open_dashboard_editor)
        dlg.visibility_changed.connect(self._on_manager_visibility_changed)
        dlg.checklist_toggle_requested.connect(self._toggle_note_checklist)
        dlg.pin_toggle_requested.connect(self._toggle_note_pin)
        dlg.color_change_requested.connect(self._change_dashboard_color)
        dlg.sync_requested.connect(self._manual_sync)
        dlg.reorder_requested.connect(self._reorder_note)
        dlg.font_scale_changed.connect(self._on_font_scale_changed)
        dlg.accepted.connect(lambda: self._on_manager_accepted(dlg))
        dlg.rejected.connect(lambda: self._on_manager_closed())
        dlg.finished.connect(lambda _r: self._on_manager_closed())
        dlg.show()
        self._manager_dlg = dlg  # prevent GC
        self._update_dashboard_sync_state()
        dlg.raise_()
        dlg.activateWindow()
        _force_foreground(dlg)
        QTimer.singleShot(50, lambda: _force_foreground(dlg))

    def _on_manager_visibility_changed(self, note_id: str, is_visible: bool):
        """Desktop pinning is local only, entirely separate from Keep's pin."""
        self._visibility[note_id] = is_visible
        _save_visibility(self._visibility)
        note = self._notes.get(note_id)
        if note is None:
            return
        win = self.windows.get(note_id)
        if is_visible:
            if win is None:
                from config import get_position
                if not get_position(note_id):
                    # Initial pop-outs match the dashboard without modifying
                    # the Google color or an existing local theme choice.
                    note.dark_mode = True
                    self._save_notes_to_disk()
                win = self._create_window(note)
                self.windows[note_id] = win
            win.title_bar.set_pinned(True)
            win._on_pin_toggle(True)
            win.show()
            win.raise_()
            QTimer.singleShot(0, lambda: self._regroup_window(note_id))
        else:
            if win is not None:
                win.save_geometry()
                win.hide()

    def _open_dashboard_editor(self, note_id=None, *, is_list=False):
        manager = getattr(self, "_manager_dlg", None)
        if manager is None:
            return
        existing = manager._editor
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        is_new = note_id is None
        note = (KeepNote(id=str(uuid.uuid4()), color_hex="#FFFFFF", dark_mode=True, is_list=is_list)
                if is_new else self._notes.get(note_id))
        if note is None:
            return
        editor = manager.create_editor(note, is_new=is_new)
        editor.save_requested.connect(lambda changes: self._save_dashboard_edit(editor, changes))
        manager.show_editor()

    def _save_dashboard_edit(self, editor, changes):
        """Commit a draft without overwriting an edit or pull that arrived meanwhile."""
        from copy import deepcopy
        note_id = editor.note_id
        original = self._notes.get(note_id)
        if editor.is_new:
            if not changes:
                editor.accept()
                return
            if original is not None:
                editor.show_error("This note already exists. Your draft has not been overwritten.")
                return
            note = KeepNote(id=note_id, color_hex="#FFFFFF", dark_mode=True,
                            is_list=editor.base["is_list"])
        else:
            if original is None:
                editor.show_error("This note was removed or its sync ID changed. Copy your draft before reopening it.")
                return
            current = edit_snapshot(original)
            touched = {"title"} if "title" in changes else set()
            if any(key in changes for key in ("text", "html", "list_items")):
                touched.update(("text", "html", "is_list", "list_items"))
            if any(current[key] != editor.base[key] for key in touched):
                editor.show_error("This note changed while you were editing. Nothing was overwritten. "
                                  "Copy your draft, then reopen the updated note.")
                return
            # Keep object identity: an in-flight create/push may still hold
            # this exact note, just as it does while a floating note is typed.
            # Replacing the object could lose a draft during UUID promotion.
            before = deepcopy(original)
            note = original
        if not changes:
            editor.accept()
            return
        if editor.is_new:
            # Persist hidden-by-default before the cache entry. A stray
            # visibility entry is harmless; an uncached visibility choice
            # must not make a new dashboard note pop out on next startup.
            self._visibility[note_id] = False
            try:
                _save_visibility(self._visibility)
            except Exception:
                self._visibility.pop(note_id, None)
                editor.show_error("Could not save desktop visibility. Your draft is still open; try again.")
                return
        self._last_edit_time[note_id] = time.monotonic()
        for key, value in changes.items():
            setattr(note, key, deepcopy(value))
        if any(key in changes for key in ("text", "html", "list_items")) and hasattr(note, "styled_doc"):
            delattr(note, "styled_doc")
        was_dirty = note_id in self._dirty
        self._notes[note_id] = note
        self._dirty.add(note_id)
        try:
            self._save_notes_to_disk()
        except Exception:
            if original is None:
                self._notes.pop(note_id, None)
                self._visibility.pop(note_id, None)
            else:
                for key in changes:
                    setattr(original, key, deepcopy(getattr(before, key)))
                if hasattr(before, "styled_doc"):
                    original.styled_doc = before.styled_doc
            if not was_dirty:
                self._dirty.discard(note_id)
            editor.show_error("Could not save securely. Your draft is still open; try again.")
            return
        self._refresh_window(note_id)
        self._refresh_manager_if_open()
        if self.sync.is_authenticated:
            self._sync_debounce.start()
        editor.accept()

    def _change_dashboard_color(self, note_id, color_hex):
        from config import KEEP_COLORS
        note = self._notes.get(note_id)
        if note is None or color_hex not in KEEP_COLORS.values() or note.color_hex == color_hex:
            return
        note.color_hex = color_hex
        self._dirty.add(note_id)
        self._last_edit_time[note_id] = time.monotonic()
        self._save_notes_to_disk()
        self._refresh_window(note_id)
        self._refresh_manager_if_open()
        if self.sync.is_authenticated:
            self._sync_debounce.start()

    def _on_manager_accepted(self, dlg):
        self._visibility = dlg.result_visibility
        _save_visibility(self._visibility)
        self._apply_visibility()

    def _on_manager_closed(self):
        self._manager_dlg = None

    def _on_font_scale_changed(self, scale: float):
        self.config["font_scale"] = scale
        save_config(self.config)
        note_window.set_font_scale(scale)
        for win in self.windows.values():
            win.refresh_font_scale()

    def _refresh_manager_if_open(self):
        dlg = getattr(self, "_manager_dlg", None)
        if dlg is not None:
            dlg.refresh(self._notes, self._visibility)
            self._update_dashboard_sync_state()

    def _apply_visibility(self):
        for note_id, note in self._notes.items():
            should_show = self._visibility.get(note_id, True)
            win = self.windows.get(note_id)
            if should_show:
                if win is None:
                    win = self._create_window(note)
                    self.windows[note_id] = win
                    win.show()
                # If the window already exists & is visible, leave its
                # z-order alone — we don't want closing the manager to
                # punch every note window to the front.
                elif not win.isVisible():
                    win.show()
            else:
                if win is not None:
                    win.save_geometry()
                    win.hide()

    # ── Taskbar toggle ─────────────────────────────────────────────────

    def _toggle_taskbar(self, show):
        self._show_in_taskbar = show
        self.config["show_in_taskbar"] = show
        save_config(self.config)
        for win in self.windows.values():
            win.set_taskbar_visible(show)
        # Apply grouping immediately for the new visibility state.
        self._apply_taskbar_grouping()
        # Group toggle is only meaningful when notes are in the taskbar.
        if hasattr(self, "_group_action"):
            self._group_action.setEnabled(show)

    def _toggle_taskbar_grouping(self, group: bool):
        self.config["group_in_taskbar"] = group
        save_config(self.config)
        self._apply_taskbar_grouping()

    def _grouping_app_id_for(self, note_id: str) -> str:
        from config import APP_NAME, APP_VERSION
        base = f"LukeCGG.{APP_NAME}.{APP_VERSION}"
        if self.config.get("group_in_taskbar", True):
            return base
        # Stable per-note id so taskbar pins survive restarts.
        return f"{base}.note.{note_id[:12]}"

    def _regroup_window(self, note_id: str) -> None:
        """Force-rebuild one window's taskbar button under the current
        AppUserModelID.

        Just calling hide()/setAppID()/show() is NOT enough on Windows:
        the shell remembers the HWND's original taskbar group and
        keeps the button in that slot. The reliable workaround is to
        destroy the underlying native HWND so the next show() creates
        a fresh one — Windows treats it as a new window and registers
        it under whatever AppUserModelID is current at that moment.

        We also need to restore geometry, because destroy() drops it.
        """
        win = self.windows.get(note_id)
        if win is None:
            return
        was_visible = win.isVisible()
        # Snapshot state we need to restore after recreating the HWND.
        geom = win.geometry()
        # Save any in-flight geometry so a follow-on save doesn't lose it.
        try:
            win.save_geometry()
        except Exception:  # noqa: BLE001
            pass

        new_app_id = self._grouping_app_id_for(note_id)

        if was_visible:
            win.hide()
        # Drop the native window. Qt will lazily recreate it on show().
        # create() forces it now so we have a winId() to attach the
        # AppUserModelID to BEFORE the window becomes visible (and is
        # registered with the taskbar).
        try:
            win.destroy(destroyWindow=True, destroySubWindows=False)
            win.create()
        except Exception as exc:  # noqa: BLE001
            log.warning("HWND recreate failed for %s: %s", note_id, exc)

        try:
            _set_window_app_id(win, new_app_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Couldn't set per-window app id for %s: %s",
                        note_id, exc)

        if was_visible:
            win.setGeometry(geom)
            win.show()
            # Re-assert AppID after show — some Qt builds reset window
            # properties during the platform-window re-attach.
            try:
                _set_window_app_id(win, new_app_id)
            except Exception:  # noqa: BLE001
                pass

    def _apply_taskbar_grouping(self):
        """Rebuild the taskbar button for every visible note under the
        current grouping setting.
        """
        for nid in list(self.windows.keys()):
            self._regroup_window(nid)

    # ── Sign in / Sign out ─────────────────────────────────────────────

    def _show_sign_out(self):
        dlg = QDialog()
        dlg.setWindowTitle("KeepDesktop – Sign Out")
        dlg.setFixedWidth(380)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        email = self.config.get("email", "Unknown")
        lay.addWidget(QLabel(f"Signed in as <b>{_escape_html(email)}</b>"))
        lay.addWidget(QLabel("Are you sure you want to sign out?"))

        clear_cb = QCheckBox("Clear live local notes (keep recovery history)")
        lay.addWidget(clear_cb)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        signout_btn = QPushButton("Sign Out")
        signout_btn.setStyleSheet(
            "QPushButton { background: #d93025; color: white; border: none;"
            " border-radius: 6px; padding: 6px 20px; font-weight: bold; }"
            "QPushButton:hover { background: #c5221f; }"
        )
        signout_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(signout_btn)
        lay.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._do_sign_out(clear_cb.isChecked())

    def _do_sign_out(self, clear_notes=False):
        try:
            self.sync.logout()
        except Exception:
            QMessageBox.warning(None, "KeepDesktop", "Could not clear all stored credentials. "
                                "Local access is disabled. Revoke this device in your Google account.")
        self._sync_timer.stop()
        self._sync_debounce.stop()
        self.config["cloud_writes_enabled"] = False
        self.config["cloud_writes_consent_version"] = 0
        self._writes_action.blockSignals(True)
        self._writes_action.setChecked(False)
        self._writes_action.blockSignals(False)

        self.config["sync_enabled"] = False
        self.config["remember_google_account"] = False
        self.config.pop("email", None)
        save_config(self.config)

        if clear_notes:
            for win in list(self.windows.values()):
                win.close()
                win.deleteLater()
            self.windows.clear()
            self._notes.clear()
            self._visibility.clear()
            self._dirty.clear()
            self.config.pop("notes_account", None)
            save_config(self.config)
            _save_visibility({})
            self._save_notes_to_disk()
            # Remove positions
            from config import POSITIONS_FILE
            if os.path.exists(POSITIONS_FILE):
                os.remove(POSITIONS_FILE)

        self._update_login_action_text()

    # ── Sync ───────────────────────────────────────────────────────────

    def _do_login(self, email, password=None, master_token=None):
        if (self.config.get("remember_google_account") is not True
                or type(self.config.get("experimental_auth_consent")) is not int
                or self.config["experimental_auth_consent"] != AUTH_CONSENT_VERSION):
            return
        try:
            ok = self.sync.login(email, password=password, master_token=master_token,
                                 remember=True, consent=True)
        except Exception:
            ok = False
            self._sync_notice.emit("Stored credentials could not be unlocked. Please sign in again.")
        if ok:
            self.config["email"] = email
            self.config["start_offline"] = False
            self._show_manager_after_sync = not any("." in nid for nid in self._notes)
            self.config["notes_account"] = email
            self.config["cloud_writes_enabled"] = self.sync.writes_enabled is True
            self._writes_action.blockSignals(True)
            self._writes_action.setChecked(self.config["cloud_writes_enabled"])
            self._writes_action.blockSignals(False)
            self.config["sync_enabled"] = True
            save_config(self.config)
            self._full_sync()
            self._sync_timer.start(SYNC_INTERVAL_MS)
            self._update_login_action_text()
            # Existing notebooks stay quiet on background startup. A first
            # uncached import opens the manager after its successful pull.
        return ok

    def _show_note_manager_if_not_open(self):
        if not self._manager_dialog_open():
            self._show_note_manager()

    def _first_launch_login(self):
        dlg = LoginDialog(first_launch=True)
        dlg.set_keep_sync(self.sync)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted and dlg.signed_in
        email, remember = dlg.get_email(), dlg._remember_login
        offline_requested = dlg._offline_requested
        dlg.deleteLater()
        if accepted:
            self.config["start_offline"] = False
            self._show_manager_after_sync = True
            if email:
                self.config["email"] = email
                self.config["notes_account"] = email
            self.config["remember_google_account"] = remember
            self.config["experimental_auth_consent"] = AUTH_CONSENT_VERSION
            self.config["cloud_writes_enabled"] = self.sync.writes_enabled is True
            self._writes_action.blockSignals(True)
            self._writes_action.setChecked(self.config["cloud_writes_enabled"])
            self._writes_action.blockSignals(False)
            self.config["sync_enabled"] = True
            save_config(self.config)
            self._full_sync()
            self._sync_timer.start(SYNC_INTERVAL_MS)
            self._update_login_action_text()
            # The first successful pull opens the manager with loaded notes.
        else:
            if offline_requested:
                self.config["start_offline"] = True
                save_config(self.config)
            if not self.windows:
                self._new_note()
            self.tray.showMessage(
                "KeepDesktop is running!",
                "Right-click the tray icon to manage notes or sign in to Google Keep.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _show_login(self):
        dlg = LoginDialog(first_launch=False)
        dlg.set_keep_sync(self.sync)
        accepted = dlg.exec() == QDialog.DialogCode.Accepted and dlg.signed_in
        email, remember = dlg.get_email(), dlg._remember_login
        dlg.deleteLater()
        if accepted:
            self.config["start_offline"] = False
            self._show_manager_after_sync = True
            if email:
                self.config["email"] = email
                self.config["notes_account"] = email
            self.config["remember_google_account"] = remember
            self.config["experimental_auth_consent"] = AUTH_CONSENT_VERSION
            self.config["cloud_writes_enabled"] = self.sync.writes_enabled is True
            self._writes_action.blockSignals(True)
            self._writes_action.setChecked(self.config["cloud_writes_enabled"])
            self._writes_action.blockSignals(False)
            self.config["sync_enabled"] = True
            save_config(self.config)
            self._full_sync()
            self._sync_timer.start(SYNC_INTERVAL_MS)
            self._update_login_action_text()
            # Open the manager only after a verified pull, not on a timer that
            # can show an empty list while the first download is still running.

    def _full_sync(self, force_resync: bool = False):
        """Run one push+pull cycle in the background.

        `force_resync` promotes the pull to a full resync. The periodic
        tick leaves it False (an incremental delta is all it needs);
        user-triggered "Sync now" passes True, because the whole point
        of pressing it is "I know something changed over there, go and
        get it" — and an incremental pull can only ever return what the
        cursor says is new, which is exactly nothing when the cursor
        has already moved past the change.
        """
        if not self.sync.is_authenticated:
            return
        # Re-entrancy guard: a slow sync (large pull, retry, etc) can
        # still be running when the next timer tick fires. Stacking
        # them double-pushes dirty notes and can race the cache.
        if getattr(self, "_sync_running", False):
            log.debug("_full_sync: previous sync still running; skipping")
            return
        self._sync_running = True
        # _is_note_busy() reads QWidget.hasFocus(), which must stay on
        # the GUI thread — compute it here, before handing off to the
        # background thread, rather than inside _sync_worker.
        busy_ids = {nid for nid in self.windows if self._is_note_busy(nid)}
        threading.Thread(target=self._sync_worker,
                         args=(busy_ids, force_resync), daemon=True).start()

    def _sync_worker(self, busy_ids: set, force_resync: bool = False):
        try:
            # Push dirty notes first — via the shared, race-safe
            # helper (see _push_one_dirty_note) so a note edited while
            # ITS push is in flight isn't silently marked clean here
            # too. This loop used to have its own inline copy of the
            # push logic, predating that fix, so the exact race it
            # closes was still fully reachable every 30s via this
            # periodic/manual sync path even after the debounce path
            # (_push_dirty_worker) was fixed.
            #
            # _push_running (NOT _sync_running) guards just this push
            # phase against _push_dirty_notes' own background thread
            # touching the same self._dirty set concurrently — see
            # _push_running's own comment for why this must be a
            # SEPARATE flag from _sync_running, scoped to end before
            # the pull below starts.
            if not self._push_running:
                self._push_running = True
                try:
                    for note_id in list(self._dirty):
                        self._push_one_dirty_note(note_id)
                finally:
                    self._push_running = False

            # Pull remote notes. Notes that are still dirty (push
            # above didn't clear them), whose window is busy, or whose
            # cache was just updated but hasn't reached the widget yet
            # (self._pending_widget_refresh -- a merge-during-push
            # result queued for the main thread, or a busy-deferred
            # remote pull still waiting for idle) won't have this
            # fetch's content applied to their widget right away (see
            # _apply_remote_notes) — hold their push-merge baseline
            # back too, or a genuine concurrent web restyle would look
            # identical to "no remote change" once the editor finally
            # catches up, and the next local push would silently
            # overwrite it right back off the server.
            try:
                remote_notes = self.sync.fetch_notes(
                    force_resync=force_resync,
                    hold_baseline_for=(
                        self._dirty | busy_ids | self._pending_widget_refresh
                    ),
                )
            except Exception as error:  # noqa: BLE001
                log.warning("Keep read failed (%s)", type(error).__name__)
                self._read_sync_failed.emit()
                return
            # Hand back to main thread via signal (QTimer.singleShot from a
            # worker thread is unreliable / silently dropped).
            log.info("Sync worker emitting %d remote notes to main thread",
                     len(remote_notes))
            self._remote_notes_ready.emit(remote_notes)
        finally:
            self._sync_running = False

    def _apply_remote_notes(self, remote_notes: list):
        """Apply remote note data on the main thread."""
        self._read_sync_error_active = False
        self.tray.setToolTip("KeepDesktop")
        from sync_merge import decide_merge, MergeAction
        log.info("Applying %d remote notes (local dirty=%s)",
                 len(remote_notes), [d[:8] for d in self._dirty])
        new_remote_ids: list[str] = []
        for rn in remote_notes:
            if rn.id in self._pending_deletes:
                # Local delete still in flight — don't resurrect.
                continue
            existing = self._notes.get(rn.id)
            if existing is None:
                log.info("New remote note %s", rn.id[:8])
                self._notes[rn.id] = rn
                if rn.id not in self._visibility:
                    # New notes coming in from another device are hidden
                    # by default — the user opts in via the Note Manager.
                    self._visibility[rn.id] = False
                    _save_visibility(self._visibility)
                    new_remote_ids.append(rn.id)
                if self._visibility.get(rn.id, False):
                    self._add_window_for_note(rn)
            else:
                # Compute merge decision with the user's editing state.
                idle_for = time.monotonic() - self._last_edit_time.get(rn.id, 0.0)
                user_busy = self._is_note_busy(rn.id)
                decision = decide_merge(
                    local=existing,
                    remote=rn,
                    is_dirty=(rn.id in self._dirty),
                    user_busy=user_busy,
                    local_rendered_doc=self._rendered_doc.get(rn.id),
                )

                if decision.action is MergeAction.SKIP_DIRTY:
                    log.info("Skipping remote update for %s (locally dirty)", rn.id[:8])
                    continue

                metadata_changed = existing.pinned != rn.pinned or existing.sort_key != rn.sort_key
                if (decision.text_changed or decision.title_changed or decision.list_changed
                        or decision.html_changed or decision.color_changed or metadata_changed):
                    from hardened_sync import snapshot
                    try:
                        self.sync._save_recovery(local=snapshot(existing), remote=snapshot(rn),
                                                 reason="before-remote-update")
                    except Exception:
                        # A failed backup must not advance the baseline ahead
                        # of the content that is still on screen.
                        self.sync.register_cached_note(existing, snapshot(existing))
                        self._dirty.add(rn.id)
                        self._sync_notice.emit("Could not save recovery before applying a cloud edit. "
                                                "The local version was retained and marked pending.")
                        continue

                # Pinning and ordering are editable surfaces too. Only adopt
                # remote metadata after SKIP_DIRTY and secure recovery, never
                # erase a pin still waiting for cloud-write opt-in or upload.
                existing.sort_key = rn.sort_key
                existing.pinned = rn.pinned

                # Diagnostics
                changes = []
                if decision.color_changed:
                    changes.append(f"color {existing.color_hex}->{rn.color_hex}")
                if decision.title_changed:
                    changes.append("title")
                if decision.text_changed:
                    changes.append("text")
                if existing.is_list != rn.is_list:
                    changes.append(f"is_list {existing.is_list}->{rn.is_list}")
                if existing.list_items != rn.list_items:
                    changes.append(
                        f"list_items ({len(existing.list_items)}->{len(rn.list_items)})"
                    )
                if decision.html_changed:
                    changes.append("formatting")
                if changes:
                    log.info("Note %s changed: %s", rn.id[:8], ", ".join(changes))

                if decision.action is MergeAction.PRESERVE_LOCAL_BODY:
                    log.warning(
                        "Note %s: remote text empty but local non-empty "
                        "(%d chars). Skipping body overwrite.",
                        rn.id[:8], len(existing.text or ""),
                    )
                    # Still adopt safe metadata (title, colour, pin).
                    # dark_mode is a per-device rendering preference, not
                    # part of Keep's data model — never let a remote
                    # colour change override it (see ADOPT_REMOTE below).
                    existing.title = rn.title
                    existing.color_hex = rn.color_hex
                    continue

                # ADOPT_REMOTE — full body update.
                existing.text = rn.text
                if decision.text_changed:
                    existing.html = ""  # text changed remotely — stale html
                # Only adopt remote html when text is non-empty (or both are empty).
                if rn.html and rn.text:
                    existing.html = rn.html
                # Carry the freshly-decoded StyledDoc across so the
                # cursor renderer (preserves empty paragraphs) is
                # used on refresh. Without this, _refresh_window
                # falls back to note.html and HTML round-trip
                # corrupts blank lines.
                rn_doc = getattr(rn, "styled_doc", None)
                if rn_doc is not None:
                    existing.styled_doc = rn_doc  # type: ignore[attr-defined]
                elif hasattr(existing, "styled_doc"):
                    # Remote no longer has a snapshot doc (e.g. legacy
                    # note); drop the stale one.
                    try:
                        delattr(existing, "styled_doc")
                    except AttributeError:
                        pass
                existing.title = rn.title
                # dark_mode is a per-device rendering preference — it's
                # never sent to Keep (the wire colour is always the light
                # hex, see config.KEEP_COLORS_DARK), so a remote colour
                # change (from the web, or from this same note open on
                # another computer) must not touch it. Forcing it off
                # here used to reset every other open instance of this
                # note back to its light variant on the very next pull.
                existing.color_hex = rn.color_hex
                existing.list_items = rn.list_items
                existing.is_list = rn.is_list
                if user_busy:
                    # The cache (existing.*) is already updated above,
                    # but the open window can't be safely re-rendered
                    # over active typing. Without a retry, this remote
                    # change is never shown: decide_merge() compares
                    # against `existing`, which now already matches
                    # `rn`, so no future pull ever sees a difference to
                    # refresh for again — and if the user's next
                    # keystroke re-derives note.text/.html from the
                    # (still stale) widget before that, their push
                    # would silently overwrite this remote change right
                    # back off the server. Retry once idle instead of
                    # dropping it.
                    log.info(
                        "Deferring refresh for %s (user editing, idle %.1fs)",
                        rn.id[:8], idle_for,
                    )
                    self._pending_widget_refresh.add(rn.id)
                    self._refresh_window_when_idle(rn.id)
                elif decision.refresh_window:
                    log.info("Refreshing window %s", rn.id[:8])
                    self._refresh_window(rn.id)

        # Remove notes that no longer exist on Keep (deleted on another
        # device). Guard against an empty remote list — that usually
        # means the fetch failed, and we don't want to wipe everything.
        # Also defer single-cycle absences (a metadata push response or
        # incremental sync sometimes echoes fewer nodes than usual)
        # via ``_missing_strikes`` so we don't flicker notes out of the
        # manager only to immediately re-add them next cycle.
        STRIKES_TO_DELETE = 2
        if remote_notes:
            remote_ids = {rn.id for rn in remote_notes}
            for nid in list(self._notes.keys()):
                if nid in remote_ids:
                    self._missing_strikes.pop(nid, None)
                    continue
                if nid in self._dirty:
                    # Local edit still being pushed — don't remove.
                    continue
                # Real Keep server ids are ``timestamp.user`` and
                # always contain a dot. Anything else is a local UUID
                # that hasn't been promoted to a server id yet (e.g.
                # _push_new_note hasn't completed) — those won't ever
                # show up in remote_ids by construction.
                if "." not in nid:
                    continue
                strikes = self._missing_strikes.get(nid, 0) + 1
                self._missing_strikes[nid] = strikes
                if strikes < STRIKES_TO_DELETE:
                    log.info(
                        "Note %s missing from sync (strike %d/%d); "
                        "deferring delete",
                        nid[:8], strikes, STRIKES_TO_DELETE,
                    )
                    continue
                log.info("Note %s removed remotely; deleting locally", nid[:8])
                if hasattr(self.sync, "_save_recovery"):
                    from hardened_sync import snapshot
                    try:
                        self.sync._save_recovery(local=snapshot(self._notes[nid]),
                                                 reason="removed-remotely")
                    except Exception:
                        self._sync_notice.emit("Could not save a recovery copy; local note was retained.")
                        continue
                self._missing_strikes.pop(nid, None)
                self._notes.pop(nid, None)
                self._rendered_doc.pop(nid, None)
                self._visibility.pop(nid, None)
                win = self.windows.pop(nid, None)
                if win:
                    win.close()
                    win.deleteLater()
            _save_visibility(self._visibility)

        self._save_notes_to_disk()
        self._refresh_manager_if_open()

        if getattr(self, "_show_manager_after_sync", False):
            self._show_manager_after_sync = False
            QTimer.singleShot(0, self._show_note_manager_if_not_open)

        # Later background pulls do not reopen the manager. The one-shot flag
        # is armed only by interactive login or a first uncached import.

    def _manager_dialog_open(self) -> bool:
        dlg = getattr(self, "_manager_dlg", None)
        return dlg is not None and dlg.isVisible()

    def _push_dirty_notes(self):
        if not self.sync.is_authenticated or not self._dirty:
            return
        # Dedicated re-entrancy guard (_push_running), deliberately
        # NOT _sync_running: this (the debounced post-edit push) and
        # _sync_worker's own "push dirty notes first" step both call
        # the same _push_one_dirty_note for whatever's currently in
        # self._dirty, from two INDEPENDENT background threads with no
        # mutual exclusion between them -- redundant concurrent pushes
        # of the same note_id would race on app_controller-level
        # bookkeeping (dirty-clearing, _pending_widget_refresh,
        # _note_merged_during_push emission) that KeepSyncV2's own
        # internal lock doesn't cover.
        #
        # _sync_running was tried here first and reverted: _full_sync
        # guards its ENTIRE cycle (push AND pull) with it, so shared
        # would mean a debounced push in flight -- which fires on
        # every edit during active typing, far more often than the
        # 30s periodic tick -- blocks that periodic cycle's pull
        # phase too, starving fetch_notes() during exactly the
        # "actively editing while a web change also landed" scenario
        # this whole push/pull split exists to handle correctly. This
        # flag only ever gates the push phase (see _sync_worker's own
        # scoped use of it), never the pull.
        if getattr(self, "_push_running", False):
            log.debug("_push_dirty_notes: a push is already running; skipping")
            return
        self._push_running = True
        threading.Thread(target=self._push_dirty_worker, daemon=True).start()

    def _push_dirty_worker(self):
        try:
            for note_id in list(self._dirty):
                self._push_one_dirty_note(note_id)
        finally:
            self._push_running = False
            self._save_requested.emit()

    def _push_one_dirty_note(self, note_id: str):
        """Push a single dirty note, race-safely. Caller must be on a
        background thread (this blocks on network I/O via push_note).

        Shared by both push triggers — the debounced post-edit push
        AND the periodic/manual full-sync's own "push dirty notes
        first" step — so the mid-push race protection below applies
        regardless of which one happens to fire. It used to live only
        in the debounce path; the periodic-sync path had its own
        unguarded copy of this same loop, so the exact race this
        method exists to prevent was still fully reachable every 30s
        via that path even after the debounce path was fixed.
        """
        note = self._notes.get(note_id)
        if not note:
            self._dirty.discard(note_id)
            return
        if _is_local_note_id(note_id):
            self._push_new_note(note)
            return
        # push_note's 3-way / format-preserving merge paths can
        # rewrite note.text/.html in place to fold in a concurrent
        # remote edit — from THIS (background) thread. The open
        # window has no idea that happened and keeps showing only
        # what the user typed; worse, the user's very next keystroke
        # re-derives note.text from the (stale) widget and clobbers
        # the merge before it's ever seen. Detect the mutation and
        # ask the GUI thread to refresh the window so the merge
        # actually reaches the screen.
        text_before, html_before = note.text, note.html
        # push_note is network-bound (it does its own full resync
        # before writing) and can easily take several seconds, all on
        # this background thread, while _on_note_changed keeps
        # mutating this SAME note object from the main thread on
        # every keystroke. If the user types more mid-push, what
        # we're about to send doesn't include that edit.
        push_started_at = time.monotonic()
        try:
            ok = self.sync.push_note(note)
        except Exception:  # noqa: BLE001
            log.exception("push_note crashed for %s", note_id[:8])
            ok = False
        if ok is not False:
            edited_during_push = (
                self._last_edit_time.get(note_id, 0.0) > push_started_at
            )
            if edited_during_push:
                # Don't clear dirty — the edit that landed while this
                # push was in flight was never sent. Without this
                # check we'd mark the note clean anyway, and since
                # nothing else ever re-dirties a note on its own, that
                # edit would silently never sync: no further push
                # would carry it, and the next periodic pull (no
                # longer blocked by SKIP_DIRTY, since dirty was
                # cleared) would overwrite the cached text with the
                # server's older copy. Leave it dirty so the next push
                # cycle sends the CURRENT widget content instead.
                log.info(
                    "v2 push: %s edited again mid-push; keeping "
                    "dirty for another push cycle", note_id[:8],
                )
            else:
                self._dirty.discard(note_id)
            if note.text != text_before or note.html != html_before:
                # Mark BEFORE emitting: the signal is queued to the
                # main thread's event loop and may not be processed
                # for a while, but a periodic sync's fetch_notes()
                # (running on ANOTHER background thread) could fire in
                # that gap -- see _pending_widget_refresh's own
                # comment for why that must not advance this note's
                # baseline yet.
                self._pending_widget_refresh.add(note_id)
                self._note_merged_during_push.emit(note_id)

    def _refresh_window_when_idle(self, note_id: str):
        """Render self._notes[note_id]'s current content into its open
        window, retrying later if the user is busy right now.

        Shared by two callers that both adopt new content into the
        note cache (push_note's merge result, or a remote pull) without
        being able to touch the widget immediately: stomping active
        typing with a snapshot computed before the user's last few
        keystrokes would lose those keystrokes. The cache is already
        correct by the time this is called — whenever the refresh
        actually lands is still correct, just possibly delayed.
        """
        win = self.windows.get(note_id)
        note = self._notes.get(note_id)
        if not note:
            return
        if not win:
            # Dashboard-only edits need no floating-widget catch-up. Leaving
            # this marker set would hold their baseline indefinitely.
            self._pending_widget_refresh.discard(note_id)
            self._refresh_manager_if_open()
            return
        if self._is_note_busy(note_id):
            QTimer.singleShot(
                int(self._edit_idle_seconds * 1000),
                lambda: self._refresh_window_when_idle(note_id),
            )
            return
        self._refresh_window(note_id)
        self._refresh_manager_if_open()

    def _add_window_for_note(self, note: KeepNote):
        if note.id in self.windows:
            return
        win = self._create_window(note)
        self.windows[note.id] = win
        if self._visibility.get(note.id, True):
            win.show()
            # show()-then-grouping so the icon attaches first.
            QTimer.singleShot(0, lambda: self._regroup_window(note.id))

    def _refresh_window(self, note_id: str):
        win = self.windows.get(note_id)
        note = self._notes.get(note_id)
        if win and note:
            # set_styled_doc/set_html/set_text below all clear() and
            # rebuild the document from scratch, which resets the
            # scrollbar to the top — jarring if the user was reading
            # further down when a sync (even a no-op one that just
            # re-confirms unchanged content) happened to land.
            # Restore the scroll position afterward; deferred one
            # event-loop tick so the widget's layout — and thus its
            # scrollbar range — has settled onto the new content
            # first (a synchronous restore can land before Qt has
            # recomputed the range for the new document length).
            scrollbar = win.text_edit.verticalScrollBar()
            saved_scroll = scrollbar.value()
            if saved_scroll:
                def _restore_scroll(sb=scrollbar, v=saved_scroll):
                    # The note (or its window) can be closed/deleted
                    # in the gap between scheduling this and the next
                    # event-loop tick actually running it (e.g. the
                    # user closes the note right after a remote pull
                    # triggers this refresh) -- sb would then be a
                    # dangling wrapper around an already-destroyed Qt
                    # C++ scrollbar, and touching it raises
                    # "wrapped C/C++ object ... has been deleted"
                    # from inside a timer callback.
                    try:
                        sb.setValue(v)
                    except RuntimeError:
                        pass
                QTimer.singleShot(0, _restore_scroll)
            win._is_list = note.is_list
            win.set_dark_mode(note.dark_mode)
            if note.is_list:
                # Real ChecklistEditor path — reflect remote items.
                win.set_list_items(note.list_items)
                win.text_edit.setVisible(False)
                win.checklist_editor.setVisible(True)
                win.fmt_toolbar.setVisible(False)
            elif getattr(note, "styled_doc", None) is not None:
                # Cursor-based render preserves empty paragraphs and
                # avoids HTML round-trip artefacts (NBSP injection,
                # line ping-pong with Keep web).
                win._syncing = True
                try:
                    win.text_edit.set_styled_doc(note.styled_doc)
                finally:
                    # Deferred clear — the highlighter's late
                    # textChanged must not read as a user edit.
                    win.end_sync_render()
                self._rendered_doc[note_id] = note.styled_doc
                win.checklist_editor.setVisible(False)
                win.text_edit.setVisible(True)
                win.fmt_toolbar.setVisible(True)
            elif note.html:
                # Preserve any local rich-text formatting the user has set.
                win.set_html(note.html)
                win.checklist_editor.setVisible(False)
                win.text_edit.setVisible(True)
                win.fmt_toolbar.setVisible(True)
            else:
                win.set_text(note.text)
                win.checklist_editor.setVisible(False)
                win.text_edit.setVisible(True)
                win.fmt_toolbar.setVisible(True)
            win.set_title(note.title)
            win._apply_color(note.color_hex)
            # The widget now actually reflects the cache -- see
            # _pending_widget_refresh's own comment for why a periodic
            # sync's fetch_notes() must not advance this note's
            # baseline before this point. Cleared at the END (not the
            # top of this method) so an exception partway through the
            # render above leaves the note correctly still marked
            # pending rather than wrongly treated as caught up.
            self._pending_widget_refresh.discard(note_id)

    def _manual_sync(self):
        if not self.sync.is_authenticated:
            QMessageBox.information(
                None, "KeepDesktop",
                "Not signed in. Use 'Sign in to Google Keep' first.",
            )
            return
        log.info("Manual sync requested")
        self.tray.showMessage(
            "KeepDesktop", "Syncing with Google Keep\u2026",
            QSystemTrayIcon.MessageIcon.Information, 2000,
        )
        # User-triggered Sync Now is also a request to realign with
        # Keep — drop any locally-imposed order so the manager mirrors
        # whatever order Keep currently reports (its sortValue field).
        # Per-note moves the user makes after this will re-establish a
        # local override via _reorder_note (which now also pushes the
        # new sortValue back to Keep).
        cleared = 0
        for n in self._notes.values():
            if n.local_order:
                n.local_order = 0
                cleared += 1
        if cleared:
            log.info("Manual sync: cleared local_order on %d notes", cleared)
            self._save_notes_to_disk()
            self._refresh_manager_if_open()
        self._full_sync(force_resync=True)

    def _periodic_sync(self):
        """Timer-driven sync.

        Most ticks are incremental — that is all a text edit needs. But
        a FORMATTING-ONLY change on the web (bold, italic, a heading)
        moves no text at all: it lives entirely in the note's
        docs-nestedModel snapshot, and if a delta bumps the revision
        without re-echoing that snapshot there is nothing in the
        response to compare against. Only a full resync surfaces it.

        So fold one in periodically, and pick the cadence by whether the
        user can actually see anything: with note windows open a stale
        note is visibly wrong, so check every few minutes; with
        everything closed there is nothing to be wrong on screen, so
        back off and keep the request count down. This is the cheap
        version of "only check open notes" — the pull is one bulk
        request either way, so what is worth tuning is how OFTEN it
        runs, not which notes it covers.
        """
        if not self.sync.is_authenticated:
            return
        self._tick_count += 1
        anything_visible = any(
            w.isVisible() for w in self.windows.values()
        )
        every = (_FULL_RESYNC_TICKS_ACTIVE if anything_visible
                 else _FULL_RESYNC_TICKS_IDLE)
        force = (self._tick_count % every) == 0
        if force:
            log.info("Periodic sync tick (full resync; %s)",
                     "windows open" if anything_visible else "all closed")
        else:
            log.info("Periodic sync tick")
        self._full_sync(force_resync=force)

    def _toggle_autostart(self, enabled):
        ok = set_autostart(enabled)
        if not ok:
            log.warning("set_autostart(%s) failed", enabled)
        # Re-read the actual on-disk state so config.json never lies
        # about what Windows will do at login.
        actual = is_autostart_enabled()
        self.config["autostart"] = actual
        save_config(self.config)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_note_manager()

    # ── Updates ────────────────────────────────────────────────────────

    def _on_update_available(self, info):
        if self._any_window_busy():
            # Don't pop a modal over someone's typing — Qt hands the
            # very next keypress to the new dialog's focused button, so
            # a keystroke meant for the note (Enter/Space) can silently
            # trigger "Update now" instead. Retry shortly instead.
            QTimer.singleShot(10000, lambda: self._on_update_available(info))
            return
        if getattr(self, "_update_prompt_open", False):
            # The startup check's retry and a manual "Check for
            # updates" click can both resolve around the same time —
            # don't stack a second modal on top of one already showing.
            return
        self._update_prompt_open = True
        try:
            prompt_and_install(None, info)
        finally:
            self._update_prompt_open = False

    def _is_note_busy(self, note_id: str) -> bool:
        """True if the user appears to be actively engaged with this
        note's window right now — recently edited it AND either the
        body or the title field currently has keyboard focus. Used to
        avoid interrupting in-progress editing with a sync-driven
        refresh, a merge result, or an update prompt.

        Checks the title field too, not just the body: a user typing
        a title is just as "busy" as one typing body text, but only
        the body was checked here originally — narrower than the
        other two busy-checks in this class started requiring."""
        win = self.windows.get(note_id)
        if not win:
            return False
        idle_for = time.monotonic() - self._last_edit_time.get(note_id, 0.0)
        if idle_for >= self._edit_idle_seconds:
            return False
        title_edit = getattr(win.title_bar, "title_edit", None)
        return bool(
            win.text_edit.hasFocus()
            or (title_edit is not None and title_edit.hasFocus())
        )

    def _any_window_busy(self) -> bool:
        return any(self._is_note_busy(note_id) for note_id in self.windows)

    def _manual_update_check(self):
        if not AUTO_UPDATE_ENABLED:
            QMessageBox.information(None, "KeepDesktop", "Automatic installer updates are disabled "
                                    "in this private build. Update only from reviewed source.")
            return
        if self._manual_update_in_progress:
            return
        self._manual_update_in_progress = True
        self.tray.showMessage(
            APP_NAME, "Checking for updates\u2026",
            QSystemTrayIcon.MessageIcon.Information, 1500,
        )
        checker = UpdateChecker()
        checker.update_available.connect(self._on_manual_update_available)
        checker.no_update.connect(self._on_manual_no_update)
        checker.error.connect(self._on_manual_update_error)
        # Hold a reference so it isn't GC'd before the worker finishes
        self._manual_checker = checker
        checker.check_async()

    def _on_manual_update_available(self, info):
        self._manual_update_in_progress = False
        # Route through the same busy-check/defer as the startup check —
        # the network check this follows can take a few seconds, long
        # enough for the user to have started typing since they clicked
        # "Check for updates".
        self._on_update_available(info)

    def _on_manual_no_update(self):
        self._manual_update_in_progress = False
        QMessageBox.information(
            None, APP_NAME,
            f"You're running the latest version ({APP_VERSION})."
        )

    def _on_manual_update_error(self, msg):
        self._manual_update_in_progress = False
        QMessageBox.warning(
            None, APP_NAME,
            f"Couldn't check for updates:\n{msg}"
        )

    def _open_log_file(self):
        log_path = os.path.join(DATA_DIR, "keepdesktop.log")
        if not os.path.exists(log_path):
            QMessageBox.information(
                None, APP_NAME,
                f"No log file yet at:\n{log_path}",
            )
            return
        try:
            os.startfile(log_path)  # Windows: opens in default text editor
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                None, APP_NAME,
                f"Couldn't open log file:\n{exc}\n\nIt's at:\n{log_path}",
            )

    def _show_about(self):
        dlg = QDialog()
        dlg.setWindowTitle(f"About {APP_NAME}")
        dlg.setMinimumWidth(420)
        v = QVBoxLayout(dlg)
        v.setSpacing(10)

        body = QLabel(
            f"<h3>{APP_NAME} {APP_VERSION}</h3>"
            "<p>Google Keep on your Windows desktop as floating sticky notes.</p>"
            "<p>Licensed under the "
            "GNU Affero General Public License v3.0 (included in LICENSE).</p>"
            f"<p>Source: github.com/{GITHUB_REPO} (copy into a browser separately).</p>"
            "<p>Google-only runtime networking. External links and in-app updater "
            "downloads are disabled.</p>"
        )
        body.setOpenExternalLinks(False)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setWordWrap(True)
        v.addWidget(body)

        btn_row = QHBoxLayout()
        check_btn = QPushButton("⬆  Check for updates…")
        check_btn.clicked.connect(lambda: (dlg.accept(), self._manual_update_check()))
        btn_row.addWidget(check_btn)

        log_btn = QPushButton("📄  Open log file")
        log_btn.clicked.connect(self._open_log_file)
        btn_row.addWidget(log_btn)

        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setDefault(True)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)

        dlg.exec()

    def _quit(self):
        manager = getattr(self, "_manager_dlg", None)
        editor = getattr(manager, "_editor", None)
        if editor is not None:
            editor.reject()
            if editor.isVisible():
                return
        # Save all window geometry
        for win in self.windows.values():
            win.save_geometry()
        # Save notes to disk
        for nid, note in self._notes.items():
            win = self.windows.get(nid)
            if win:
                note.text = win.get_text()
                note.title = win.get_title()
                note.color_hex = win.color_hex
                note.dark_mode = bool(getattr(win, "dark_mode", False))
                note.html = win.get_html()
        self._save_notes_to_disk()
        # No new network writes at shutdown. The encrypted dirty queue resumes
        # next launch. In-flight writes have a durable pause marker.
        QApplication.quit()
