"""Native settings with fictional data and mocked OS/account operations only."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

import app_controller
from app_controller import AppController
from note_manager import NoteManagerDialog


@pytest.fixture
def controller(qapp, monkeypatch):
    settings = {"start_offline": True, "autostart": False, "show_in_taskbar": False,
                "group_in_taskbar": True, "cloud_writes_enabled": True, "font_scale": 1.0}
    disk = SimpleNamespace(startup=False)
    saved = []
    startup = MagicMock(side_effect=lambda enabled: setattr(disk, "startup", enabled) or True)
    engine = MagicMock(is_authenticated=False, writes_enabled=False)
    engine.pause_file.exists.return_value = False
    callbacks = {name: MagicMock() for name in ("account", "sync", "recovery", "conflicts", "about")}
    monkeypatch.setattr("hardened_sync.HardenedKeepSync", lambda **kwargs: engine)
    monkeypatch.setattr(app_controller, "load_config", lambda: dict(settings))
    monkeypatch.setattr(app_controller, "save_config", lambda value: saved.append(deepcopy(value)))
    monkeypatch.setattr(app_controller, "load_notes", lambda: {})
    monkeypatch.setattr(app_controller, "save_notes", MagicMock())
    monkeypatch.setattr(app_controller, "_load_visibility", lambda: {})
    monkeypatch.setattr(app_controller, "_save_visibility", MagicMock())
    monkeypatch.setattr(app_controller, "is_autostart_enabled", lambda: disk.startup)
    monkeypatch.setattr(app_controller, "set_autostart", startup)
    monkeypatch.setattr(app_controller, "_force_foreground", MagicMock())
    monkeypatch.setattr(app_controller.note_window, "set_font_scale", MagicMock())
    monkeypatch.setattr(AppController, "_start_initial_session", lambda self: None)
    monkeypatch.setattr(AppController, "_apply_taskbar_grouping", lambda self: None)
    monkeypatch.setattr(AppController, "_on_login_action", lambda self: callbacks["account"]())
    monkeypatch.setattr(AppController, "_manual_sync", lambda self: callbacks["sync"]())
    monkeypatch.setattr(AppController, "_show_recovery_copies", lambda self: callbacks["recovery"]())
    monkeypatch.setattr(AppController, "_resolve_sync_conflicts", lambda self: callbacks["conflicts"]())
    monkeypatch.setattr(AppController, "_show_about", lambda self: callbacks["about"]())
    # Avoid a deferred native-focus retry outliving this isolated test widget.
    original_single_shot = QTimer.singleShot
    monkeypatch.setattr(QTimer, "singleShot", lambda interval, callback:
                        None if interval == 50 else original_single_shot(interval, callback))
    ctl = AppController()
    ctl._full_sync = MagicMock()
    ctl._show_note_manager()
    ctl._test_settings = SimpleNamespace(disk=disk, saved=saved, startup=startup,
                                         engine=engine, callbacks=callbacks)
    try:
        yield ctl
    finally:
        for timer in (ctl._sync_timer, ctl._sync_debounce, ctl._save_debounce):
            timer.stop()
        dialog = ctl._manager_dlg
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        ctl.tray.hide()
        ctl.tray.deleteLater()
        ctl.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_opening_settings_does_not_change_os_sync_or_notes(controller):
    ctl = controller
    before = deepcopy(ctl.config)
    dlg = ctl._manager_dlg
    QTest.mouseClick(dlg.findChild(QPushButton, "openSettings"), Qt.MouseButton.LeftButton)
    assert dlg._pages.currentIndex() == 1
    assert ctl.config == before
    ctl._test_settings.startup.assert_not_called()
    ctl.sync.set_writes_enabled.assert_not_called()
    ctl.sync.fetch_notes.assert_not_called()
    assert not ctl._dirty
    assert not any(call.called for call in ctl._test_settings.callbacks.values())


def test_windows_startup_and_tray_are_synchronized(controller):
    ctl = controller
    check = ctl._manager_dlg._setting_checks["autostart"]
    check.click()
    assert check.isChecked() and ctl._autostart_action.isChecked()
    assert ctl._test_settings.disk.startup and ctl.config["autostart"]
    assert ctl._test_settings.saved[-1]["autostart"] is True
    ctl._autostart_action.trigger()
    assert not check.isChecked() and not ctl._test_settings.disk.startup
    assert ctl._test_settings.startup.call_count == 2
    assert not ctl._dirty


def test_failed_startup_change_restores_actual_state_and_explains(controller, monkeypatch):
    ctl = controller
    ctl._test_settings.startup.side_effect = None
    ctl._test_settings.startup.return_value = False
    warning = MagicMock()
    monkeypatch.setattr(app_controller.QMessageBox, "warning", warning)
    check = ctl._manager_dlg._setting_checks["autostart"]
    check.click()
    assert not check.isChecked() and not ctl._autostart_action.isChecked()
    assert ctl.config["autostart"] is False
    warning.assert_called_once()


def test_reopening_settings_reads_external_startup_change_without_toggling(controller):
    ctl = controller
    ctl._test_settings.disk.startup = True
    ctl._manager_dlg._show_settings()
    assert ctl._manager_dlg._setting_checks["autostart"].isChecked()
    assert ctl._autostart_action.isChecked()
    ctl._test_settings.startup.assert_not_called()
    ctl.sync.set_writes_enabled.assert_not_called()


def test_taskbar_grouping_and_font_changes_are_saved_and_apply_immediately(controller):
    ctl = controller
    dlg = ctl._manager_dlg
    window = MagicMock()
    ctl.windows["fictional"] = window
    assert not dlg._setting_checks["group_in_taskbar"].isEnabled()
    dlg._setting_checks["show_in_taskbar"].click()
    assert ctl._show_in_taskbar and ctl._taskbar_action.isChecked()
    assert dlg._setting_checks["group_in_taskbar"].isEnabled()
    window.set_taskbar_visible.assert_called_once_with(True)
    dlg._setting_checks["group_in_taskbar"].click()
    assert ctl.config["group_in_taskbar"] is False and not ctl._group_action.isChecked()
    dlg._font_scale_combo.setCurrentIndex(dlg._FONT_SCALE_PRESETS.index(1.25))
    assert ctl.config["font_scale"] == 1.25
    window.refresh_font_scale.assert_called_once()
    assert ctl._test_settings.saved[-1]["font_scale"] == 1.25
    assert not ctl._dirty


@pytest.mark.parametrize("key", ["show_in_taskbar", "group_in_taskbar", "font_scale"])
def test_failed_preference_save_does_not_apply_or_display_unsaved_change(controller, monkeypatch, key):
    ctl = controller
    dlg = ctl._manager_dlg
    before = deepcopy(ctl.config)
    if key == "group_in_taskbar":
        ctl._toggle_taskbar(True)
        before = deepcopy(ctl.config)
    monkeypatch.setattr(app_controller, "save_config", MagicMock(side_effect=OSError("fictional failure")))
    warning = MagicMock()
    monkeypatch.setattr(app_controller.QMessageBox, "warning", warning)
    if key == "font_scale":
        dlg._font_scale_combo.setCurrentIndex(dlg._FONT_SCALE_PRESETS.index(1.5))
        assert dlg._font_scale_combo.currentData() == before[key]
    else:
        dlg._setting_checks[key].click()
        assert dlg._setting_checks[key].isChecked() is before[key]
    assert ctl.config == before
    warning.assert_called_once()


def test_tray_changes_update_in_app_preferences_without_signal_echo(controller):
    ctl = controller
    changed = MagicMock()
    dlg = ctl._manager_dlg
    dlg.preference_changed.connect(changed)
    ctl._taskbar_action.trigger()
    assert dlg._setting_checks["show_in_taskbar"].isChecked()
    changed.assert_not_called()


def test_sync_resume_uses_existing_confirmation_and_cancel_restores_both_controls(controller, monkeypatch):
    ctl = controller
    ctl._toggle_cloud_writes(False)
    ctl.sync.reset_mock()
    question = MagicMock(return_value=app_controller.QMessageBox.StandardButton.No)
    monkeypatch.setattr(app_controller.QMessageBox, "question", question)
    ctl._manager_dlg._setting_checks["cloud_writes_enabled"].click()
    question.assert_called_once()
    assert ctl.config["cloud_writes_enabled"] is False
    assert not ctl._writes_action.isChecked()
    assert not ctl._manager_dlg._setting_checks["cloud_writes_enabled"].isChecked()
    ctl.sync.set_writes_enabled.assert_not_called()


def test_requested_sync_is_not_mistaken_for_effective_permission(controller):
    ctl = controller
    dlg = ctl._manager_dlg
    ctl.sync.is_authenticated = True
    ctl.sync.writes_enabled = False
    ctl._update_dashboard_sync_state()
    assert dlg._setting_checks["cloud_writes_enabled"].isChecked()
    assert "safety-paused" in dlg._settings_sync_status.text()
    ctl.sync.set_writes_enabled.assert_not_called()


def test_sync_resume_from_settings_keeps_the_uncertain_write_warning(controller, monkeypatch):
    ctl = controller
    ctl._toggle_cloud_writes(False)
    ctl.sync.reset_mock()
    ctl.sync.pause_file.exists.return_value = True
    question = MagicMock(return_value=app_controller.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(app_controller.QMessageBox, "question", question)
    ctl._manager_dlg._setting_checks["cloud_writes_enabled"].click()
    assert "interrupted or unverified" in question.call_args.args[2]
    ctl.sync.set_writes_enabled.assert_called_once_with(True, acknowledge_pause=True)
    ctl._full_sync.assert_called_once_with(force_resync=True)
    assert ctl.config["cloud_writes_enabled"] is True
    assert not ctl.sync.writes_enabled  # Requested mode is not permission to upload.


def test_settings_navigation_stays_selected_across_refresh_and_home(controller):
    dlg = controller._manager_dlg
    dlg._show_settings()
    dlg.refresh({}, {})
    assert dlg._settings_button.isChecked()
    assert not any(button.isChecked() for button in dlg._nav_buttons.values())
    dlg._home_button.click()
    assert not dlg._settings_button.isChecked()
    assert dlg._nav_buttons["notes"].isChecked()


@pytest.mark.parametrize("text,callback", [("Sign in to Google Keep…", "account"),
    ("View saved recovery copies…", "recovery"), ("Resolve sync conflicts…", "conflicts"),
    ("About KeepDesktop…", "about")])
def test_account_and_tools_are_explicit_actions(controller, text, callback):
    ctl = controller
    button = next(b for b in ctl._manager_dlg.findChildren(QPushButton) if b.text() == text)
    assert not ctl._test_settings.callbacks[callback].called
    button.click()
    ctl._test_settings.callbacks[callback].assert_called_once()


def test_sync_now_is_disabled_offline_and_only_runs_on_click(controller):
    ctl = controller
    button = ctl._manager_dlg._settings_sync_button
    button.click()
    ctl._test_settings.callbacks["sync"].assert_not_called()
    ctl.sync.is_authenticated = True
    ctl._update_dashboard_sync_state()
    button.click()
    ctl._test_settings.callbacks["sync"].assert_called_once()


def test_unknown_or_untyped_setting_requests_are_ignored(controller):
    before = deepcopy(controller.config)
    controller._on_settings_preference_changed("email", True)
    controller._on_settings_preference_changed("autostart", "true")
    assert controller.config == before
    controller._test_settings.startup.assert_not_called()


def test_settings_fit_small_window_and_scroll_to_tools(controller, qapp):
    dlg = controller._manager_dlg
    dlg.resize(640, 480)
    dlg._show_settings()
    qapp.processEvents()
    assert dlg._settings_scroll.verticalScrollBar().maximum() > 0
    assert dlg._settings_scroll.horizontalScrollBar().maximum() == 0
    about = next(b for b in dlg.findChildren(QPushButton) if b.text() == "About KeepDesktop…")
    dlg._settings_scroll.ensureWidgetVisible(about)
    assert dlg._settings_scroll.verticalScrollBar().value() > 0
    assert dlg._setting_checks["autostart"].isChecked() is False


def test_saved_settings_restore_without_side_effects(qapp):
    saved = {"autostart": True, "show_in_taskbar": True, "group_in_taskbar": False,
             "cloud_writes_enabled": False, "font_scale": 1.5, "email": "fictional@example.test"}
    dlg = NoteManagerDialog({}, {}, preferences=saved)
    changed = MagicMock()
    dlg.preference_changed.connect(changed)
    dlg.set_preferences(saved)
    assert dlg._setting_checks["autostart"].isChecked()
    assert dlg._setting_checks["show_in_taskbar"].isChecked()
    assert not dlg._setting_checks["group_in_taskbar"].isChecked()
    assert not dlg._setting_checks["cloud_writes_enabled"].isChecked()
    assert dlg._font_scale_combo.currentData() == 1.5
    assert "email" not in dlg._preferences
    changed.assert_not_called()
    dlg.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
