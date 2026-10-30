"""Fictional cloud catalogs, archives and default-on sync; no account access."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent
from PySide6.QtWidgets import QLabel, QTextEdit

from app_controller import AppController
from hardened_sync import HardenedKeepSync, SyncedNotes, snapshot, comparison
from keep_protocol.client import KeepClient, KeepError
from keep_protocol.models import Note
from note_manager import NoteManagerDialog
from note_model import KeepNote


def connection(tmp_path):
    client = KeepClient(MagicMock())
    client._post = MagicMock()
    engine = HardenedKeepSync(recovery_dir=tmp_path / "recovery")
    engine._client = client
    engine._authenticated = True
    return engine, client


def page(nodes=None, labels=None, version="v1", **fields):
    result = {"nodes": nodes or [], "toVersion": version, "truncated": False, **fields}
    if labels is not None:
        result["userInfo"] = {"labels": labels}
    return result


def test_cloud_archive_and_catalog_decode_together(tmp_path, qapp):
    engine, client = connection(tmp_path)
    client._post.return_value = page([
        {"id": "1.active", "title": "Fictional active note", "labelIds": [{"labelId": "ideas"}]},
        {"id": "2.archive", "isArchived": True, "text": "Fictional archive",
         "labelIds": [{"labelId": "ideas", "deleted": "1970-01-01T00:00:00.000Z"},
                      {"labelId": "removed", "deleted": "2026-01-01T00:00:00Z"}]},
        {"id": "3.trash", "trashState": 1}], labels=[
            {"mainId": "ideas", "name": "Ideas"}, {"mainId": "empty", "name": "Empty label"}])
    assert not engine.writes_enabled
    result = engine.fetch_notes()
    assert engine.writes_enabled
    assert result.label_names == ["Empty label", "Ideas"]
    assert [note.id for note in result] == ["1.active", "2.archive"]
    assert result[1].archived and result[1].labels == ["Ideas"]
    assert engine._remote_snapshot(client.notes["2.archive"])["unavailable"]
    assert len(client._post.call_args_list) == 1  # Just the verified read.


def test_labels_omitted_from_incremental_response_are_retained(tmp_path):
    _, client = connection(tmp_path)
    client._post.return_value = page(labels=[{"mainId": "a", "name": "Ideas"}])
    client.sync()
    client._post.return_value = page(version="v2")
    client.sync()
    assert client.labels == {"a": "Ideas"}
    client._post.return_value = page(labels=[], version="v3")
    client.sync()
    assert client.labels == {}


@pytest.mark.parametrize("bad", [None, "wrong", [None], [{"mainId": "x"}],
    [{"mainId": 1, "name": "Ideas"}], [{"mainId": "x", "name": 3}],
    [{"mainId": "x", "name": "A"}, {"mainId": "x", "name": "B"}]])
def test_invalid_label_catalog_rolls_back_notes_labels_and_cursor(tmp_path, bad):
    _, client = connection(tmp_path)
    client.notes = {"1.before": Note.from_server({"id": "1.before", "text": "Before"})}
    client.labels = {"a": "Before"}
    client.target_version = "before"
    previous = deepcopy((client.notes, client.labels, client.target_version))
    response = page([{"id": "2.after"}], version="after")
    response["userInfo"] = {"labels": bad}
    client._post.return_value = response
    with pytest.raises(KeepError):
        client.sync()
    assert (client.notes, client.labels, client.target_version) == previous


def test_later_page_failure_rolls_back_earlier_label_replacement(tmp_path):
    _, client = connection(tmp_path)
    client.labels = {"old": "Before"}
    client.target_version = "before"
    client._post.side_effect = [page(labels=[{"mainId": "new", "name": "After"}],
                                   truncated=True), KeepError("fictional offline")]
    with pytest.raises(KeepError):
        client.sync()
    assert client.labels == {"old": "Before"}
    assert client.target_version == "before"


def test_read_failure_resumes_requested_mode_only_after_a_success(tmp_path, qapp):
    engine, client = connection(tmp_path)
    assert engine.writes_requested and not engine.writes_enabled
    assert engine.create_note("Must wait", "Fictional") is None
    client._post.return_value = page()
    engine.fetch_notes()
    assert engine.writes_enabled
    client._post.side_effect = KeepError("fictional offline")
    with pytest.raises(KeepError):
        engine.fetch_notes()
    assert engine.writes_requested and not engine.writes_enabled
    assert not engine.pause_file.exists()
    client._post.side_effect = None
    engine.fetch_notes()
    assert engine.writes_enabled
    engine.set_writes_enabled(False)
    engine.fetch_notes()
    assert not engine.writes_requested and not engine.writes_enabled


def test_automatic_resume_and_default_enable_cannot_clear_write_journal(tmp_path, qapp):
    engine, client = connection(tmp_path)
    engine._start_write("fictional-uncertain-create")
    before = engine.pause_file.read_bytes()
    engine.set_writes_enabled(True)
    client._post.return_value = page()
    engine.fetch_notes()
    assert not engine.writes_enabled
    assert engine.pause_file.read_bytes() == before
    engine.set_writes_enabled(True, acknowledge_pause=True)
    assert not engine.writes_enabled  # Explicit review still requires a read.
    engine.fetch_notes()
    assert engine.writes_enabled


@pytest.mark.parametrize("saved,expected", [({}, True),
    ({"cloud_writes_enabled": False}, True),
    ({"cloud_writes_policy_version": 2, "cloud_writes_enabled": False}, False),
    ({"cloud_writes_policy_version": 2, "cloud_writes_enabled": True}, True)])
def test_default_migration_is_once_and_preserves_new_manual_pauses(monkeypatch, saved, expected):
    import config
    monkeypatch.setattr(config, "load_json", lambda *args: dict(saved))
    result = config.load_config()
    assert result["cloud_writes_enabled"] is expected
    assert result["cloud_writes_policy_version"] == 2


def test_labels_and_archives_roundtrip_in_the_same_encrypted_cache(tmp_path, qapp, monkeypatch):
    import config
    from secure_storage import load_protected
    monkeypatch.setattr(config, "NOTES_FILE", str(tmp_path / "notes.dat"))
    engine, _ = connection(tmp_path)
    note = KeepNote(id="1.archived", title="Fictional archive", archived=True, labels=["Ideas"])
    ctl = SimpleNamespace(_notes={note.id: note}, _dirty={note.id}, sync=engine,
                          _cloud_labels=["Ideas", "Empty label"], _visibility={})
    AppController._save_notes_to_disk(ctl)
    assert b"Fictional archive" not in (tmp_path / "notes.dat").read_bytes()
    assert load_protected(tmp_path / "notes.dat")[note.id]["archived"]
    ctl._notes = {}
    ctl._cloud_labels = []
    AppController._load_notes_from_disk(ctl)
    assert ctl._notes[note.id].archived
    assert ctl._notes[note.id].labels == ["Ideas"]
    assert ctl._cloud_labels == ["Ideas", "Empty label"]
    assert note.id in ctl._dirty


def test_read_metadata_does_not_turn_a_body_edit_into_a_conflict(qapp):
    original = KeepNote(id="1.note", text="Fictional content")
    updated = deepcopy(original)
    updated.labels = ["Ideas"]
    updated.archived = True
    assert comparison(snapshot(original)) == comparison(snapshot(updated))
    assert snapshot(updated)["labels"] == ["Ideas"]


def test_sidebar_catalog_and_archive_filters_are_real_and_read_only(qapp):
    notes = {n.id: n for n in [KeepNote(id="1.active", labels=["Ideas"]),
        KeepNote(id="2.archive", title="<literal title>", text="<img src='https://invalid.test'>",
                 archived=True, pinned=True, labels=["Ideas"])]}
    dlg = NoteManagerDialog(notes, {}, cloud_labels=["Ideas", "Empty label"])
    def shown():
        return [c.note_id for c in dlg._pinned_grid.cards + dlg._others_grid.cards]
    try:
        assert shown() == ["1.active"]
        assert "Empty label" in dlg._label_buttons
        dlg._label_buttons["Empty label"].click()
        assert shown() == []
        dlg._label_buttons["Ideas"].click()
        assert set(shown()) == set(notes)
        dlg._nav_buttons["archive"].click()
        assert shown() == ["2.archive"]
        assert dlg._composer.isHidden()
        archived = dlg._cards["2.archive"]
        assert archived.pin.isHidden() and archived.desktop.isHidden() and archived.palette.isHidden()
        viewer = dlg.show_archived_note(notes["2.archive"])
        body = viewer.findChild(QTextEdit)
        assert body.isReadOnly() and body.toPlainText() == notes["2.archive"].text
        assert any(label.text() == "<literal title>" for label in viewer.findChildren(QLabel))
        viewer.close()
        dlg._return_to_notes()
        assert shown() == ["1.active"]
    finally:
        dlg.close()
        dlg.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_qt_result_signal_preserves_the_empty_label_catalog(qapp):
    class Emitter(QObject):
        ready = Signal(object)
    emitter = Emitter()
    received = []
    emitter.ready.connect(received.append)
    emitter.ready.emit(SyncedNotes([], ["Empty label"]))
    assert received[0].label_names == ["Empty label"]
    emitter.ready.disconnect()
    emitter.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("url,allowed", [("https://buza.dev", True),
    ("https://buza.dev/", False), ("https://buza.dev.evil.test", False),
    ("http://buza.dev", False), ("file:///C:/test", False), ("https://example.test", False)])
def test_about_homepage_is_a_fixed_user_clicked_exception(monkeypatch, url, allowed):
    opener = MagicMock()
    monkeypatch.setattr("app_controller.QDesktopServices.openUrl", opener)
    AppController._open_about_homepage(url)
    assert opener.called is allowed
    if allowed:
        assert opener.call_args.args[0].toString() == "https://buza.dev"


def test_about_keeps_maker_upstream_credit_and_license(qapp, monkeypatch):
    captured = []
    def inspect(dialog):
        captured.extend(label.text() for label in dialog.findChildren(QLabel))
        return 0
    monkeypatch.setattr("app_controller.QDialog.exec", inspect)
    controller = SimpleNamespace(_open_about_homepage=AppController._open_about_homepage,
                                 _open_log_file=MagicMock())
    AppController._show_about(controller)
    body = " ".join(captured)
    assert "Made by Tyler Buza" in body and 'href="https://buza.dev"' in body
    assert "LukeCGG" in body and "Affero" in body
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.mark.parametrize("refs", [None, {}, [None], [{"labelId": 1}], [{"labelId": "x", "deleted": "bad"}]])
def test_malformed_note_label_references_fail_the_read_atomically(tmp_path, refs):
    _, client = connection(tmp_path)
    client.target_version = "before"
    client.labels = {"a": "Before"}
    client._post.return_value = page([{"id": "1.bad", "labelIds": refs}],
                                   labels=[{"mainId": "b", "name": "After"}])
    with pytest.raises(KeepError):
        client.sync()
    assert client.target_version == "before"
    assert client.labels == {"a": "Before"} and client.notes == {}


def test_archive_metadata_updates_do_not_overwrite_pending_body_edits(tmp_path, qapp):
    engine, _ = connection(tmp_path)
    note = KeepNote(id="1.fiction", text="Queued local draft", labels=["Old label"])
    remote = KeepNote(id=note.id, text="Cloud text", archived=True, labels=["Ideas"])
    window = MagicMock()
    controller = SimpleNamespace(sync=engine, _notes={note.id: note}, _dirty={note.id},
        _pending_deletes=set(), _last_edit_time={}, _rendered_doc={}, _visibility={note.id: True},
        _missing_strikes={}, windows={note.id: window}, _pending_widget_refresh=set(),
        _is_note_busy=lambda nid: False, _save_notes_to_disk=MagicMock(),
        _refresh_manager_if_open=MagicMock(), tray=MagicMock())
    AppController._apply_remote_notes(controller, SyncedNotes([remote], ["Ideas", "Empty label"]))
    assert note.text == "Queued local draft" and note.archived and note.labels == ["Ideas"]
    assert controller._dirty == {note.id} and controller._cloud_labels == ["Ideas", "Empty label"]
    assert controller._visibility[note.id] is False
    window.close.assert_called_once()


@pytest.mark.parametrize("method,args", [
    (AppController._toggle_note_pin, ()), (AppController._reorder_note, ("top",)),
    (AppController._change_dashboard_color, ("#FFFFFF",)),
    (AppController._on_note_deleted, ()), (AppController._on_note_changed, ()),
    (AppController._on_manager_visibility_changed, (True,)),
])
def test_archived_controller_actions_are_non_mutating(method, args):
    note = KeepNote(id="1.archived", archived=True)
    before = deepcopy(note)
    controller = SimpleNamespace(_notes={note.id: note}, windows={})
    method(controller, note.id, *args)
    assert note == before


def test_manual_resume_cannot_drop_an_uncheckpointed_create_marker(tmp_path):
    engine, _ = connection(tmp_path)
    engine._create_pending = "2.fiction"
    engine._start_write("create-awaiting-local-checkpoint", server_id="2.fiction")
    before = engine.pause_file.read_bytes()
    engine.set_writes_enabled(True, acknowledge_pause=True)
    assert engine.pause_file.read_bytes() == before


def test_many_cloud_labels_scroll_without_hiding_archive_navigation(qapp):
    labels = [f"Fictional label {i:02}" for i in range(60)]
    dialog = NoteManagerDialog({}, {}, cloud_labels=labels)
    dialog.resize(800, 480)
    dialog.show()
    qapp.processEvents()
    try:
        assert dialog._label_host.grab().toImage().pixelColor(2, 2).name() == "#202124"
        scrollbar = dialog._label_scroll.verticalScrollBar()
        assert scrollbar.maximum() > 0
        dialog._label_scroll.ensureWidgetVisible(dialog._label_buttons[labels[-1]])
        assert scrollbar.value() > 0
        assert dialog._nav_buttons["archive"].isVisible()
        dialog._label_buttons[labels[-1]].click()
        assert dialog._view == "label:" + labels[-1]
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
