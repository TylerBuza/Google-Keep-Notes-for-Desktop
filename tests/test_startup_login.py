from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QDialog

from app_controller import AppController, LoginDialog
from keep_protocol.auth import AUTH_CONSENT_VERSION


def startup_controller(config=None, *, authenticated=False, windows=None):
    return SimpleNamespace(config=dict(config or {}),
                           sync=SimpleNamespace(is_authenticated=authenticated),
                           windows=windows or {}, _do_login=MagicMock(),
                           _first_launch_login=MagicMock(), _new_note=MagicMock())


@pytest.mark.parametrize("config", [{}, {"seen_login": True}, {"seen_login": False},
    {"start_offline": False}, {"start_offline": "yes"}])
def test_unsigned_startup_prompts_until_an_explicit_offline_choice(monkeypatch, config):
    schedule = MagicMock()
    monkeypatch.setattr("app_controller.QTimer.singleShot", schedule)
    controller = startup_controller(config)
    AppController._start_initial_session(controller)
    schedule.assert_called_once_with(300, controller._first_launch_login)
    controller._new_note.assert_not_called()
    controller._do_login.assert_not_called()


def test_authenticated_startup_does_not_prompt_or_create_an_offline_note(monkeypatch):
    schedule = MagicMock()
    monkeypatch.setattr("app_controller.QTimer.singleShot", schedule)
    controller = startup_controller(authenticated=True)
    AppController._start_initial_session(controller)
    schedule.assert_not_called()
    controller._new_note.assert_not_called()


@pytest.mark.parametrize("has_notes", [False, True])
def test_explicit_offline_startup_skips_stored_login_and_preserves_existing_notes(monkeypatch, has_notes):
    schedule = MagicMock()
    monkeypatch.setattr("app_controller.QTimer.singleShot", schedule)
    controller = startup_controller({"start_offline": True, "email": "test@example.test",
        "remember_google_account": True, "experimental_auth_consent": AUTH_CONSENT_VERSION},
        windows={"existing": object()} if has_notes else {})
    AppController._start_initial_session(controller)
    controller._do_login.assert_not_called()
    schedule.assert_not_called()
    assert controller._new_note.call_count == (0 if has_notes else 1)


@pytest.mark.parametrize("resume_succeeds", [False, True])
def test_remembered_login_is_resumed_before_deciding_whether_to_prompt(monkeypatch, resume_succeeds):
    schedule = MagicMock()
    monkeypatch.setattr("app_controller.QTimer.singleShot", schedule)
    controller = startup_controller({"email": "test@example.test", "remember_google_account": True,
                                     "experimental_auth_consent": AUTH_CONSENT_VERSION})
    controller._do_login.side_effect = lambda email: setattr(controller.sync, "is_authenticated", resume_succeeds)
    AppController._start_initial_session(controller)
    controller._do_login.assert_called_once_with("test@example.test")
    assert schedule.call_count == (0 if resume_succeeds else 1)
    controller._new_note.assert_not_called()


@pytest.mark.parametrize("config", [
    {"email": "test@example.test", "remember_google_account": True},
    {"email": "test@example.test", "remember_google_account": False,
     "experimental_auth_consent": AUTH_CONSENT_VERSION},
    {"email": "test@example.test", "remember_google_account": True, "experimental_auth_consent": True},
])
def test_startup_prompt_never_implies_credential_or_network_consent(monkeypatch, config):
    monkeypatch.setattr("app_controller.QTimer.singleShot", MagicMock())
    controller = startup_controller(config)
    AppController._start_initial_session(controller)
    controller._do_login.assert_not_called()


def login_controller():
    controller = startup_controller({"start_offline": False}, windows={"existing": object()})
    controller.tray = MagicMock()
    controller._writes_action = MagicMock()
    controller._full_sync = MagicMock()
    controller._sync_timer = MagicMock()
    controller._update_login_action_text = MagicMock()
    controller._show_note_manager_if_not_open = MagicMock()
    return controller


@pytest.mark.parametrize("offline_requested", [False, True])
def test_only_the_offline_button_persists_skipping_future_prompts(monkeypatch, offline_requested):
    dialog = MagicMock(signed_in=False, _remember_login=False, _offline_requested=offline_requested)
    dialog.exec.return_value = QDialog.DialogCode.Rejected
    dialog.get_email.return_value = ""
    monkeypatch.setattr("app_controller.LoginDialog", MagicMock(return_value=dialog))
    save = MagicMock()
    monkeypatch.setattr("app_controller.save_config", save)
    controller = login_controller()
    AppController._first_launch_login(controller)
    assert controller.config["start_offline"] is offline_requested
    assert save.call_count == (1 if offline_requested else 0)
    controller._full_sync.assert_not_called()
    controller._new_note.assert_not_called()


@pytest.mark.parametrize("method", [AppController._first_launch_login, AppController._show_login])
def test_successful_signin_clears_the_offline_startup_preference(monkeypatch, method):
    dialog = MagicMock(signed_in=True, _remember_login=True, _offline_requested=False)
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.get_email.return_value = "test@example.test"
    monkeypatch.setattr("app_controller.LoginDialog", MagicMock(return_value=dialog))
    monkeypatch.setattr("app_controller.save_config", MagicMock())
    monkeypatch.setattr("app_controller.QMessageBox.information", MagicMock())
    monkeypatch.setattr("app_controller.QTimer.singleShot", MagicMock())
    controller = login_controller()
    controller.config["start_offline"] = True
    method(controller)
    assert controller.config["start_offline"] is False
    assert controller.config["cloud_writes_enabled"] is False
    controller._full_sync.assert_called_once()
    assert controller._show_manager_after_sync is True


@pytest.mark.parametrize("has_cloud_cache", [False, True])
def test_remembered_first_import_opens_manager_but_existing_notebooks_stay_quiet(monkeypatch, has_cloud_cache):
    monkeypatch.setattr("app_controller.save_config", MagicMock())
    controller = login_controller()
    controller.config.update({"remember_google_account": True,
                              "experimental_auth_consent": AUTH_CONSENT_VERSION})
    controller.sync = MagicMock()
    controller.sync.login.return_value = True
    controller._notes = {"1.cloud": object()} if has_cloud_cache else {"local-uuid": object()}
    AppController._do_login(controller, "test@example.test")
    assert controller._show_manager_after_sync is not has_cloud_cache


def test_fresh_config_defaults_to_login_not_offline(monkeypatch):
    import config
    monkeypatch.setattr(config, "load_json", lambda path, default: {})
    assert config.load_config()["start_offline"] is False


def test_offline_button_does_not_start_google_or_enable_remembering(qapp, monkeypatch):
    from PySide6.QtWebEngineWidgets import QWebEngineView
    navigate = MagicMock()
    monkeypatch.setattr(QWebEngineView, "setUrl", navigate)
    dialog = LoginDialog(first_launch=True)
    try:
        assert not dialog._offline_requested
        dialog._use_offline()
        assert dialog._offline_requested
        assert not dialog._remember_login
        assert not dialog._risk_consent.isChecked()
        navigate.assert_not_called()
    finally:
        import shiboken6
        shiboken6.delete(dialog)
