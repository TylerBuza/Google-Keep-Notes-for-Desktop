from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app_controller import AppController, LoginDialog
from hardened_sync import HardenedKeepSync, snapshot
from keep_sync import KeepNote


@pytest.mark.parametrize("domain,secure,accepted", [
    ("accounts.google.com", True, True), (".google.com", True, True),
    ("evilgoogle.com", True, False), ("google.com.evil.test", True, False),
    ("accounts.google.com", False, False)])
def test_login_cookie_must_be_secure_and_from_google(domain, secure, accepted):
    import threading
    cookie = MagicMock()
    cookie.name.return_value = b"oauth_token"
    cookie.value.return_value = b"oauth2_4/dummy-token"
    cookie.domain.return_value = domain
    cookie.isSecure.return_value = secure
    dialog = SimpleNamespace(_captured_token=None, _signals=MagicMock(),
                             _login_started=True, _cancelled=threading.Event())
    LoginDialog._on_cookie(dialog, cookie)
    assert bool(dialog._signals.token_found.emit.called) is accepted


def test_failed_delete_keeps_note_visible_and_pending_edits():
    note = KeepNote(id="1.test", text="desktop edit")
    controller = SimpleNamespace(_pending_deletes={note.id}, _notes={note.id: note},
                                 _dirty={note.id}, windows={note.id: MagicMock()})
    AppController._finish_delete(controller, note.id, False)
    assert controller._notes[note.id] is note
    assert note.id in controller._dirty
    assert note.id in controller.windows
    controller.windows[note.id].close.assert_not_called()


def test_pending_edits_and_baselines_survive_encrypted_cache_restart(tmp_path, monkeypatch, qapp):
    import config
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.dat"))
    monkeypatch.setattr(config, "LEGACY_NOTES_FILE", str(tmp_path / "notes.json"))
    engine = HardenedKeepSync(recovery_dir=tmp_path / "recovery")
    note = KeepNote(id="1.test", text="offline edit")
    engine.register_cached_note(note, snapshot(KeepNote(id=note.id, text="last synced")))
    controller = SimpleNamespace(_notes={note.id: note}, _dirty={note.id}, sync=engine)
    AppController._save_notes_to_disk(controller)
    restored = SimpleNamespace(_notes={}, _dirty=set(), _visibility={},
                               sync=HardenedKeepSync(recovery_dir=tmp_path / "recovery"))
    AppController._load_notes_from_disk(restored)
    assert restored._notes[note.id].text == "offline edit"
    assert note.id in restored._dirty
    assert restored.sync.baselines[note.id]["text"] == "last synced"
    assert b"offline edit" not in (tmp_path / "notes.dat").read_bytes()


def test_created_note_is_saved_before_creation_journal_is_cleared(monkeypatch):
    import config
    monkeypatch.setattr(config, "load_positions", lambda: {})
    monkeypatch.setattr("app_controller._save_visibility", MagicMock())
    note = KeepNote(id="temporary-uuid", text="new note")
    saved = MagicMock()
    engine = MagicMock()
    engine.create_note.return_value = KeepNote(id="1.created", text="new note")
    engine.acknowledge_created_note.side_effect = lambda note_id: saved.assert_called_once()
    controller = SimpleNamespace(sync=engine, _notes={note.id: note}, _dirty={note.id},
                                 _visibility={}, windows={}, _save_notes_to_disk=saved,
                                 _save_requested=MagicMock())
    AppController._push_new_note(controller, note)
    assert "1.created" in controller._notes
    assert "1.created" in controller._dirty
    engine.acknowledge_created_note.assert_called_once_with("1.created")


def test_private_build_never_downloads_or_runs_installer(monkeypatch):
    import updater
    import urllib.request
    urlopen = MagicMock(side_effect=AssertionError("No installer requests allowed"))
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert updater.fetch_latest_release() is None
    assert updater.download_installer("https://example.test/installer.exe") is None
    assert updater.run_installer_and_quit("unused.exe") is False
    urlopen.assert_not_called()


def test_failed_signout_still_disables_startup_login_and_writes(monkeypatch):
    monkeypatch.setattr("app_controller.QMessageBox.warning", MagicMock())
    save = MagicMock()
    monkeypatch.setattr("app_controller.save_config", save)
    engine = MagicMock()
    engine.logout.side_effect = OSError("credential store locked")
    controller = SimpleNamespace(sync=engine, _sync_timer=MagicMock(),
                                 _sync_debounce=MagicMock(), _writes_action=MagicMock(),
                                 config={"email": "test@example.test", "sync_enabled": True,
                                         "cloud_writes_enabled": True},
                                 _update_login_action_text=MagicMock())
    AppController._do_sign_out(controller)
    assert "email" not in controller.config
    assert controller.config["sync_enabled"] is False
    assert controller.config["cloud_writes_enabled"] is False
    save.assert_called_once_with(controller.config)


def _pull_controller(tmp_path):
    engine = HardenedKeepSync(recovery_dir=tmp_path / "recovery")
    note = KeepNote(id="1.test", text="previous local content")
    return SimpleNamespace(sync=engine, _notes={note.id: note}, _dirty=set(),
                           _pending_deletes=set(), _last_edit_time={}, _rendered_doc={},
                           _visibility={}, _missing_strikes={}, windows={},
                            _pending_widget_refresh=set(), _is_note_busy=lambda nid: False,
                            _refresh_window=MagicMock(), _sync_notice=MagicMock(),
                            _save_notes_to_disk=MagicMock(), _refresh_manager_if_open=MagicMock(),
                            tray=MagicMock())


def test_incoming_cloud_changes_save_previous_local_version(tmp_path, qapp):
    from secure_storage import load_protected
    controller = _pull_controller(tmp_path)
    AppController._apply_remote_notes(controller, [KeepNote(id="1.test", text="cloud content")])
    assert controller._notes["1.test"].text == "cloud content"
    paths = list(controller.sync.recovery_dir.glob("*.dat"))
    assert load_protected(paths[0])["local"]["text"] == "previous local content"
    assert load_protected(paths[0])["remote"]["text"] == "cloud content"


def test_failed_recovery_blocks_incoming_overwrite_and_retains_old_baseline(tmp_path, qapp):
    controller = _pull_controller(tmp_path)
    controller.sync._save_recovery = MagicMock(side_effect=OSError("disk full"))
    AppController._apply_remote_notes(controller, [KeepNote(id="1.test", text="cloud content")])
    assert controller._notes["1.test"].text == "previous local content"
    assert "1.test" in controller._dirty
    assert controller.sync.baselines["1.test"]["text"] == "previous local content"


def test_private_installer_is_per_user_and_does_not_auto_launch_silently():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "installer.iss").read_text(encoding="utf-8")
    assert 'https://github.com/TylerBuza/Keep-Desktop' in script
    assert 'DefaultDirName={localappdata}\\Programs\\{#MyAppName}' in script
    assert 'PrivilegesRequired=lowest' in script
    assert 'PrivilegesRequiredOverridesAllowed=' not in script
    assert 'skipifsilent' in script
    assert '8B1A7C4E-3F2D-4A91-9B2E-2A4D5E6F7A8B' not in script
    assert 'Type: filesandordirs; Name: "{app}"' not in script


def test_failed_pull_does_not_masquerade_as_a_successful_empty_notebook(caplog):
    from keep_protocol.client import KeepError
    engine = MagicMock()
    engine.fetch_notes.side_effect = KeepError("dummy-secret-detail")
    controller = SimpleNamespace(sync=engine, _push_running=True, _dirty=set(),
        _pending_widget_refresh=set(), _sync_running=True, _read_sync_failed=MagicMock(),
        _remote_notes_ready=MagicMock())
    AppController._sync_worker(controller, set())
    controller._remote_notes_ready.emit.assert_not_called()
    controller._read_sync_failed.emit.assert_called_once_with()
    assert controller._sync_running is False
    assert "dummy-secret-detail" not in caplog.text


def test_verified_empty_pull_is_still_delivered_as_a_success():
    engine = MagicMock()
    engine.fetch_notes.return_value = []
    controller = SimpleNamespace(sync=engine, _push_running=True, _dirty=set(),
        _pending_widget_refresh=set(), _sync_running=True, _read_sync_failed=MagicMock(),
        _remote_notes_ready=MagicMock())
    AppController._sync_worker(controller, set())
    controller._remote_notes_ready.emit.assert_called_once_with([])
    controller._read_sync_failed.emit.assert_not_called()
    assert controller._sync_running is False


def test_read_failure_pauses_writes_and_notifies_once_without_a_modal(monkeypatch):
    save = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr("app_controller.save_config", save)
    monkeypatch.setattr("app_controller.QMessageBox.warning", warning)
    controller = SimpleNamespace(config={"cloud_writes_enabled": True}, sync=MagicMock(),
        _writes_action=MagicMock(), _update_login_action_text=MagicMock(), tray=MagicMock(),
        _read_sync_error_active=False)
    AppController._on_read_sync_failed(controller)
    AppController._on_read_sync_failed(controller)
    assert controller.config["cloud_writes_enabled"] is False
    controller.sync.set_writes_enabled.assert_called_with(False)
    controller.tray.showMessage.assert_called_once()
    warning.assert_not_called()


def test_interactive_login_opens_manager_only_after_one_verified_pull(tmp_path, qapp, monkeypatch):
    schedule = MagicMock()
    monkeypatch.setattr("app_controller.QTimer.singleShot", schedule)
    controller = _pull_controller(tmp_path)
    controller._show_manager_after_sync = True
    controller._show_note_manager_if_not_open = MagicMock()
    controller._read_sync_error_active = True
    AppController._apply_remote_notes(controller, [])
    schedule.assert_called_once_with(0, controller._show_note_manager_if_not_open)
    assert controller._read_sync_error_active is False
    assert controller._show_manager_after_sync is False
    AppController._apply_remote_notes(controller, [])
    assert schedule.call_count == 1


@pytest.mark.parametrize("titled", [False, True])
def test_manager_renders_note_text_literally_without_html_artifacts(qapp, titled):
    from note_manager import NoteCard
    from PySide6.QtCore import Qt
    note = KeepNote(id="synthetic", title="Literal <b> & title" if titled else "",
                    text="Literal <img src='https://not-google.test/x'> & text")
    card = NoteCard(note, False)
    try:
        assert card.title.text() == note.title
        assert card.body.text() == note.text
        assert card.title.textFormat() == Qt.TextFormat.PlainText
        assert card.body.textFormat() == Qt.TextFormat.PlainText
    finally:
        card.deleteLater()
