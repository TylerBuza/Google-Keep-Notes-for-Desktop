from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from hardened_sync import HardenedKeepSync, snapshot, note_from_snapshot
from keep_sync import KeepNote
from keep_protocol.client import KeepClient, KeepError
from keep_protocol.models import Note
from keep_protocol.nested_model import StyledDoc, Paragraph, StyleRun, encode_full_replace
from secure_storage import load_protected


def server(text="base", **fields):
    return Note.from_server({"id": "1.test", "type": "NOTE", "text": text,
                             "indexableText": text, "title": "title", "color": "YELLOW",
                             **fields})


@pytest.fixture
def sync(tmp_path, qapp):
    instance = HardenedKeepSync(writes_enabled=True, recovery_dir=tmp_path / "recovery",
                                on_notice=MagicMock(), on_conflict=MagicMock())
    instance._authenticated = True
    instance._client = MagicMock(spec=KeepClient)
    remote = server()
    instance._client.notes = {remote.id: remote}
    instance._server_notes = {remote.id: remote}
    instance._client.is_snapshot_stale.return_value = False
    instance._client.pop_stale_snapshot_ids.return_value = set()
    base = KeepNote(id="1.test", title="title", text="base")
    instance.register_cached_note(base, snapshot(base))
    return instance


def local(text="desktop", **fields):
    return KeepNote(id="1.test", title="title", text=text, **fields)


def assert_no_cloud_write(instance):
    for name in ("update_note_legacy_text", "update_text_diff", "update_note_doc",
                 "update_note_metadata", "update_list_diff", "create_note", "trash_note"):
        getattr(instance._client, name).assert_not_called()


def test_read_only_default_blocks_every_mutation(sync):
    sync.set_writes_enabled(False)
    assert sync.push_note(local()) is False
    assert sync.push_metadata(local(), is_pinned=True) is False
    assert sync.create_note("title", "text") is None
    assert sync.delete_note("1.test") is False
    assert_no_cloud_write(sync)


@pytest.mark.parametrize("kind", ["plain", "formatted", "checklist"])
@pytest.mark.parametrize("pinned", [False, True])
def test_pin_only_uses_guarded_metadata_write_not_body_diff(sync, kind, pinned):
    before = not pinned
    base = local("base", pinned=before)
    remote = server(isPinned=before)
    if kind == "formatted":
        doc = StyledDoc(sct_id="sct.pin", paragraphs=[Paragraph(runs=[StyleRun(text="base")])])
        base.styled_doc = doc
        remote.sct_id = doc.sct_id
        remote.serialized_chunks = [json.dumps(encode_full_replace(doc, current_text_length=0))]
    elif kind == "checklist":
        base = local("", is_list=True, pinned=before)
        remote = server("", type="LIST", isPinned=before)
    sync.register_cached_note(base, snapshot(base))
    sync._client.notes[base.id] = remote
    sync._server_notes[base.id] = remote
    changed = deepcopy(base)
    changed.pinned = pinned

    def inspect_write(*args, **kwargs):
        assert load_protected(sync.pause_file)["reason"] == "write-in-flight"
        records = list(sync.recovery_dir.glob("*.dat"))
        assert records
        assert load_protected(records[-1])["local"]["pinned"] is pinned

    sync._client.update_note_metadata.side_effect = inspect_write
    assert sync.push_note(changed) is True
    assert sync._client.update_note_metadata.call_args.kwargs["is_pinned"] is pinned
    sync._client.update_note_metadata.assert_called_once()
    for method in ("update_note_legacy_text", "update_text_diff", "update_note_doc", "update_list_diff"):
        getattr(sync._client, method).assert_not_called()
    assert sync.baselines[base.id]["pinned"] is pinned
    assert not sync.pause_file.exists()


def test_pin_only_stays_local_while_cloud_writes_are_off(sync):
    sync.set_writes_enabled(False)
    assert sync.push_note(local("base", pinned=True)) is False
    assert_no_cloud_write(sync)
    sync._client.sync.assert_not_called()
    assert sync.baselines["1.test"]["pinned"] is False


def test_failed_pin_write_pauses_without_advancing_baseline(sync):
    sync._client.update_note_metadata.side_effect = KeepError("synthetic uncertain response")
    assert sync.push_note(local("base", pinned=True)) is False
    assert sync.baselines["1.test"]["pinned"] is False
    assert not sync.writes_enabled
    assert sync.pause_file.exists()
    assert sync.push_note(local("base", pinned=True)) is False
    sync._client.update_note_metadata.assert_called_once()


def test_pin_only_does_not_overwrite_a_concurrent_remote_body_edit(sync):
    sync._client.notes["1.test"] = server("phone changed the body")
    assert sync.push_note(local("base", pinned=True)) is False
    assert_no_cloud_write(sync)
    assert "1.test" in sync.conflicts


def test_pin_only_refuses_stale_snapshot(sync):
    sync._client.is_snapshot_stale.return_value = True
    assert sync.push_note(local("base", pinned=True)) is False
    assert_no_cloud_write(sync)
    assert sync.baselines["1.test"]["pinned"] is False


def test_body_and_pin_edit_still_carries_pin_in_guarded_body_write(sync):
    assert sync.push_note(local("new body", pinned=True)) is True
    written = sync._client.update_note_legacy_text.call_args.args[0]
    assert written.raw["isPinned"] is True
    assert sync.baselines["1.test"]["pinned"] is True


def test_uncontested_edit_has_encrypted_recovery_before_write(sync):
    note = local()
    assert sync.push_note(note) is True
    sync._client.update_note_legacy_text.assert_called_once()
    records = list(sync.recovery_dir.glob("*.dat"))
    assert len(records) == 1
    backup = load_protected(records[0])
    assert backup["local"]["text"] == "desktop"
    assert backup["remote"]["text"] == "base"
    assert b"desktop" not in records[0].read_bytes()
    assert sync.baselines[note.id]["text"] == "desktop"


def test_concurrent_text_edits_preserve_both_and_never_pick_a_winner(sync):
    sync._client.notes["1.test"] = server("phone")
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)
    backup = load_protected(sync.conflicts["1.test"]["path"])
    assert backup["local"]["text"] == "desktop"
    assert backup["remote"]["text"] == "phone"
    assert sync.baselines["1.test"]["text"] == "base"
    sync.on_conflict.assert_called_once_with("1.test")
    assert sync.push_note(local()) is False
    sync.on_conflict.assert_called_once()


def test_missing_baseline_does_not_assume_local_is_authoritative(sync):
    sync.baselines.clear()
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)


def test_missing_or_trashed_remote_preserves_unsynced_local_edits(sync):
    sync._client.notes.clear()  # v2 mirror intentionally still has old entry.
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)
    assert load_protected(sync.conflicts["1.test"]["path"])["local"]["text"] == "desktop"


def test_concurrent_title_edit_cannot_bypass_guard_via_metadata(sync):
    sync._client.notes["1.test"] = server(title="phone title")
    assert sync.push_metadata(local(), is_pinned=True) is False
    assert_no_cloud_write(sync)


def test_concurrent_formatting_is_a_conflict_even_with_same_text(sync):
    doc = StyledDoc(sct_id="sct.x", paragraphs=[Paragraph(runs=[StyleRun(text="base", bold=True)])])
    remote = server()
    remote.sct_id = "sct.x"
    remote.serialized_chunks = [json.dumps(encode_full_replace(doc, current_text_length=0))]
    sync._client.notes["1.test"] = remote
    note = local("base", html="<p><i>base</i></p>")
    assert sync.push_note(note) is False
    assert_no_cloud_write(sync)
    assert sync.conflicts["1.test"]["remote"]["styled_doc"]["paragraphs"][0]["runs"][0]["bold"]


def test_choose_remote_backs_up_latest_typing_before_discard(sync):
    sync._client.notes["1.test"] = server("phone")
    note = local()
    assert sync.push_note(note) is False
    note.text = "more typing after conflict"
    chosen = sync.resolve_conflict(note.id, "remote", current_note=note)
    assert chosen.text == "phone"
    assert any(load_protected(path)["local"]["text"] == note.text
               for path in sync.recovery_dir.glob("*.dat"))
    assert_no_cloud_write(sync)


def test_choose_local_cannot_overwrite_a_newer_unseen_google_edit(sync):
    sync._client.notes["1.test"] = server("phone")
    note = local()
    assert sync.push_note(note) is False
    sync.resolve_conflict(note.id, "local", current_note=note)
    sync._client.notes["1.test"] = server("even newer phone edit")
    assert sync.push_note(note) is False
    assert_no_cloud_write(sync)


def test_prefetch_failure_blocks_push_and_durably_pauses_writes(sync):
    sync._client.sync.side_effect = KeepError("offline")
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)
    assert not sync.writes_enabled
    restarted = HardenedKeepSync(writes_enabled=True, recovery_dir=sync.recovery_dir)
    assert not restarted.writes_enabled


def test_stale_snapshot_blocks_push(sync):
    sync._client.is_snapshot_stale.return_value = True
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)


def test_recovery_failure_blocks_cloud_write(sync, monkeypatch):
    monkeypatch.setattr("hardened_sync.save_protected", MagicMock(side_effect=OSError("disk full")))
    assert sync.push_note(local()) is False
    assert_no_cloud_write(sync)


def test_uncertain_create_cannot_automatically_duplicate_note(sync):
    sync._client.create_note.side_effect = KeepError("timed out")
    assert sync.create_note("title", "new text") is None
    assert sync.create_note("title", "new text") is None
    assert sync._client.create_note.call_count == 1
    assert not sync.writes_enabled


def test_delete_refuses_concurrent_remote_changes(sync):
    sync._client.notes["1.test"] = server("phone edited before delete")
    assert sync.delete_note("1.test") is False
    assert_no_cloud_write(sync)


def test_typing_during_network_write_does_not_poison_baseline(sync):
    note = local("submitted")
    def change_during_write(*args, **kwargs):
        note.text = "typed while request was in flight"
    sync._client.update_note_legacy_text.side_effect = change_during_write
    assert sync.push_note(note) is True
    assert note.text == "typed while request was in flight"
    assert sync.baselines[note.id]["text"] == "submitted"


def test_plain_text_angle_brackets_and_unicode_roundtrip(qapp):
    note = local("<not-html> 😀\nsecond line")
    assert note_from_snapshot(snapshot(note)).text == note.text


def test_logout_clears_memory_and_both_persistent_stores(sync, monkeypatch):
    clear = MagicMock()
    monkeypatch.setattr("config.clear_token", clear)
    sync._email = "test@example.test"
    sync.logout()
    clear.assert_called_once_with("test@example.test")
    assert sync._client is None
    assert not sync.is_authenticated
    assert not sync.writes_enabled
    assert not sync.baselines


def test_logout_failure_still_blocks_all_further_writes(sync, monkeypatch):
    monkeypatch.setattr("config.clear_token", MagicMock(side_effect=OSError("locked")))
    with pytest.raises(OSError):
        sync.logout()
    assert sync._client is None
    assert not sync.is_authenticated
    assert not sync.writes_enabled


def test_write_intent_exists_before_network_mutation(sync):
    def inspect(*args, **kwargs):
        assert load_protected(sync.pause_file)["reason"] == "write-in-flight"
    sync._client.update_note_legacy_text.side_effect = inspect
    assert sync.push_note(local()) is True
    assert not sync.pause_file.exists()


def test_new_note_remains_journaled_until_local_id_checkpoint(sync):
    created = server("new text", id="2.new")
    sync._client.create_note.return_value = created
    result = sync.create_note("title", "new text")
    assert result.id == "2.new"
    assert load_protected(sync.pause_file)["server_id"] == "2.new"
    restarted = HardenedKeepSync(writes_enabled=True, recovery_dir=sync.recovery_dir)
    assert not restarted.writes_enabled
    assert sync.create_note("another", "note") is None
    sync._client.create_note.assert_called_once()
    sync.acknowledge_created_note(result.id)
    assert not sync.pause_file.exists()


def test_switching_notebook_accounts_is_rejected_before_auth(sync, monkeypatch):
    monkeypatch.setattr("hardened_sync.load_config", lambda: {"notes_account": "old@example.test"})
    auth = MagicMock()
    monkeypatch.setattr("keep_sync_v2.KeepSyncV2.login", auth)
    assert sync.login("different@example.test", master_token="dummy") is False
    auth.assert_not_called()


def test_concurrent_checklist_changes_are_not_overwritten(sync):
    from types import SimpleNamespace
    base = local("☐ row", is_list=True, list_items=[{"text": "row", "checked": False}])
    sync.register_cached_note(base, snapshot(base))
    remote = server(type="LIST")
    remote.list_items = [SimpleNamespace(text="row", checked=True)]
    sync._client.notes["1.test"] = remote
    changed = local("☐ desktop row", is_list=True,
                    list_items=[{"text": "desktop row", "checked": False}])
    assert sync.push_note(changed) is False
    assert_no_cloud_write(sync)


def test_dirty_pull_does_not_advance_conflict_baseline(sync, monkeypatch):
    monkeypatch.setattr("keep_sync_v2.KeepSyncV2.fetch_notes", lambda *args: [local("phone")])
    sync.fetch_notes(hold_baseline_for={"1.test"})
    assert sync.baselines["1.test"]["text"] == "base"


def test_google_rejecting_keep_scope_signs_out_instead_of_claiming_verified_sync(sync, monkeypatch):
    sync._client.sync.side_effect = KeepError("Google rejected session", status=403)
    monkeypatch.setattr("config.clear_token", MagicMock())
    with pytest.raises(KeepError):
        sync.fetch_notes()
    assert not sync.is_authenticated
    assert not sync.writes_enabled
    assert sync._client is None
    sync.on_notice.assert_called_once()
