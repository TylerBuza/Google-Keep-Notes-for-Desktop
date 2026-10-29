"""Conservative policy layer over the unofficial Keep protocol.

No blind conflict winner, no cloud writes without opt-in, no automatic replay
after an uncertain write, and encrypted recovery copies before every mutation.
This does NOT turn Google's private protocol into a supported public API.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
import uuid

from config import DATA_DIR, load_config, save_config
from keep_sync import KeepNote
from keep_sync_v2 import KeepSyncV2, _wire_color_to_hex, html_to_styled_doc
from keep_protocol.client import KeepError
from keep_protocol.nested_model import (
    decode_chunks, styled_doc_to_dict, styled_doc_from_dict, to_html,
    StyledDoc, Paragraph, StyleRun,
)
from secure_storage import save_protected, load_protected


def snapshot(note: KeepNote) -> dict:
    doc = None if note.is_list else getattr(note, "styled_doc", None)
    if not note.is_list and doc is None:
        # Canonicalize Qt's HTML before comparing formatting-only edits.
        doc = (html_to_styled_doc(note.html) if note.html else
               StyledDoc(paragraphs=[Paragraph(runs=[StyleRun(text=line)])
                                     for line in note.text.split("\n")]))
    styled = styled_doc_to_dict(doc) if doc is not None else None
    if styled is not None:
        styled.pop("sct_id", None)
        styled.pop("revision", None)
        for paragraph in styled.get("paragraphs", []):
            paragraph.pop("heading_id", None)
    items = [{"text": row.get("text", ""), "checked": bool(row.get("checked")),
              "indent": int(row.get("indent", 0))} for row in note.list_items]
    return {"id": note.id, "title": note.title, "text": note.text,
            "html": to_html(doc) if doc is not None else "",
            "styled_doc": styled, "color_hex": note.color_hex,
            "pinned": note.pinned, "sort_key": note.sort_key,
            "is_list": note.is_list, "list_items": items}


def comparison(record: dict) -> dict:
    # HTML spelling and server ordering do not determine content identity.
    return {key: value for key, value in record.items() if key not in ("html", "sort_key")}


def note_from_snapshot(record: dict) -> KeepNote:
    fields = {key: record[key] for key in (
        "id", "title", "text", "html", "color_hex", "pinned", "sort_key",
        "is_list", "list_items") if key in record}
    note = KeepNote(**deepcopy(fields))
    doc = styled_doc_from_dict(record.get("styled_doc"))
    if doc is not None:
        note.styled_doc = doc
    return note


class HardenedKeepSync(KeepSyncV2):
    def __init__(self, *, writes_enabled=False, on_notice=None, on_conflict=None,
                 recovery_dir=None):
        super().__init__()
        self._lock = threading.RLock()
        self.on_notice = on_notice or (lambda message: None)
        self.on_conflict = on_conflict or (lambda note_id: None)
        self.recovery_dir = Path(recovery_dir or Path(DATA_DIR) / "recovery")
        self.pause_file = self.recovery_dir.parent / "write-pause.dat"
        self.writes_enabled = writes_enabled and not self.pause_file.exists()
        self.baselines: dict[str, dict] = {}
        self.conflicts: dict[str, dict] = {}
        self._recovery_paths = {}
        self._create_pending = None

    def login(self, email, master_token=None, password=None):
        with self._lock:
            cfg = load_config()
            owner = cfg.get("notes_account")
            if owner and owner.casefold() != email.casefold():
                self.writes_enabled = False
                self.on_notice("These local notes belong to a different Google account. "
                               "Sign out and choose Clear live local notes before switching accounts. "
                               "Saved recovery copies are retained.")
                return False
            ok = super().login(email, master_token=master_token, password=password)
            if ok:
                cfg["notes_account"] = email
                save_config(cfg)
            return ok

    def set_writes_enabled(self, enabled):
        with self._lock:
            if enabled:
                self.pause_file.unlink(missing_ok=True)
            self.writes_enabled = bool(enabled)

    def _start_write(self, reason="write-in-flight", **details):
        # A crash during a request must not silently replay it on restart.
        save_protected(self.pause_file, {"paused": True, "reason": reason, **details})

    def _finish_write(self):
        if self._create_pending is None:
            self.pause_file.unlink(missing_ok=True)

    def acknowledge_created_note(self, note_id):
        """Called only after the controller durably saves the new server id."""
        with self._lock:
            if self._create_pending != note_id:
                raise KeepError("Unexpected creation checkpoint.")
            self._create_pending = None
            self._finish_write()

    def _pause(self):
        self.writes_enabled = False
        try:
            save_protected(self.pause_file, {"paused": True, "reason": "unverified-write"})
        except Exception:
            pass  # Still disable in memory and report the problem to the GUI.
        self.on_notice("Cloud writes were paused because a sync/write could not be verified. "
                       "Local edits are retained. Check the connection, use Sync now, then "
                       "explicitly re-enable cloud writes. Do not blindly retry a newly "
                       "created note: the first request may already have succeeded.")

    def register_cached_note(self, note, baseline):
        if baseline:
            self.baselines[note.id] = deepcopy(baseline)
            self._base_text[note.id] = baseline.get("text", "")
            self._base_doc[note.id] = styled_doc_from_dict(baseline.get("styled_doc"))

    def _save_recovery(self, *, local=None, remote=None, reason="before-write"):
        record = {"reason": reason, "local": local, "remote": remote,
                  "baseline": self.baselines.get((local or remote or {}).get("id"))}
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        # Deduplicate only in memory. Filenames must not reveal a plaintext
        # content hash that someone could use to test guesses about a note.
        path = self._recovery_paths.get(digest)
        if path is None:
            path = self.recovery_dir / (uuid.uuid4().hex + ".dat")
        if path.exists():
            load_protected(path)  # An unreadable backup is not a safe backup.
        else:
            save_protected(path, {**record, "saved_at": datetime.now(timezone.utc).isoformat()})
        self._recovery_paths[digest] = path
        return str(path)

    def _remote_snapshot(self, server):
        if server is None:
            return {"missing": True}
        if server.is_trashed or server.is_deleted or server.is_archived:
            return {"id": server.id, "unavailable": True, "raw": server.raw}
        note = KeepNote(id=server.id, title=server.title,
                        text=server.indexable_text or server.text,
                        color_hex=_wire_color_to_hex(server.color),
                        pinned=server.is_pinned, sort_key=int(server.sort_value or 0),
                        is_list=server.type == "LIST")
        if note.is_list:
            items = self._client.get_checkboxes(server) if server.sct_id else []
            note.list_items = ([{"text": row.text, "checked": row.checked, "indent": row.indent}
                                for row in items] if items else
                               [{"text": row.text, "checked": row.checked} for row in server.list_items])
            note.text = "\n".join(("☑" if row["checked"] else "☐") + " " + row["text"]
                                  for row in note.list_items)
        elif server.serialized_chunks:
            doc = decode_chunks(server.serialized_chunks)
            if doc is None or (not doc.plain_text.strip() and note.text.strip()):
                raise KeepError("The server note could not be decoded safely.")
            note.text = doc.plain_text
            note.styled_doc = doc
        return snapshot(note)

    def _before_push(self, keep_note, server):
        # A full pull may have removed the note while v2's mirror still
        # contains an old entry. Only the authoritative client cache counts.
        server = self._client.notes.get(keep_note.id)
        local = snapshot(keep_note)
        remote = self._remote_snapshot(server)
        baseline = self.baselines.get(keep_note.id)
        mismatch = (remote.get("missing") or remote.get("unavailable") or
                    (comparison(local) != comparison(remote) and
                     (baseline is None or comparison(baseline) != comparison(remote))))
        path = self._save_recovery(local=local, remote=remote,
                                   reason="conflict" if mismatch else "before-write")
        if mismatch:
            conflict = {"local": local, "remote": remote, "path": path}
            old = self.conflicts.get(keep_note.id)
            self.conflicts[keep_note.id] = conflict
            if old != conflict:
                self.on_conflict(keep_note.id)
            return False
        self.conflicts.pop(keep_note.id, None)
        if server is not None and keep_note.is_list != (server.type == "LIST"):
            raise KeepError("Change existing note types in the official Keep app instead.")
        self._start_write()
        return True

    def resolve_conflict(self, note_id, choice, current_note=None):
        """Acknowledge the SAVED remote version, not an unseen newer revision.

        A fresh pre-write pull still runs next time. If Google changed again,
        another conflict is raised rather than overwriting that new change.
        """
        with self._lock:
            conflict = self.conflicts[note_id]
            remote = conflict["remote"]
            if remote.get("missing") or remote.get("unavailable"):
                raise KeepError("Original note was removed/archived. Recover a copy locally instead.")
            if choice not in ("local", "remote"):
                raise ValueError("Choose local or remote")
            if current_note is not None:
                self._save_recovery(local=snapshot(current_note), remote=remote,
                                    reason="resolve-" + choice)
            self.baselines[note_id] = deepcopy(remote)
            self._base_text[note_id] = remote["text"]
            self._base_doc[note_id] = styled_doc_from_dict(remote.get("styled_doc"))
            self.conflicts.pop(note_id)
            return note_from_snapshot(remote) if choice == "remote" else None

    def fetch_notes(self, force_resync=False, hold_baseline_for=None):
        hold = hold_baseline_for or set()
        with self._lock:
            notes = super().fetch_notes(force_resync, hold)
            for note in notes:
                if note.id not in hold:
                    self.baselines[note.id] = snapshot(note)
            return notes

    def push_note(self, note):
        with self._lock:
            if not self.writes_enabled or not self.is_authenticated or self._create_pending:
                return False
            submitted = deepcopy(note)  # GUI typing cannot mutate an in-flight write.
            try:
                old_sort = self.baselines.get(note.id, {}).get("sort_key")
                ok = super().push_note(submitted)
                if ok:
                    if submitted.sort_key and old_sort != submitted.sort_key:
                        self._client.update_note_metadata(self._server_notes[note.id],
                                                          sort_value=submitted.sort_key)
                    self.baselines[note.id] = snapshot(submitted)
                    self._finish_write()
                elif note.id not in self.conflicts:
                    self._pause()
                return ok
            except Exception:
                self._pause()
                return False

    def push_metadata(self, note, *, is_pinned=None, sort_value=None, new_color=None):
        with self._lock:
            if not self.writes_enabled or not self.is_authenticated or self._create_pending:
                return False
            submitted = deepcopy(note)
            if is_pinned is not None:
                submitted.pinned = bool(is_pinned)
            if sort_value is not None:
                submitted.sort_key = sort_value
            if new_color is not None:
                submitted.color_hex = _wire_color_to_hex(new_color)
            try:
                self._client.sync(full=True)
                server = self._client.notes.get(note.id)
                if self._client.is_snapshot_stale(note.id):
                    raise KeepError("The server snapshot is stale.")
                if not self._before_push(submitted, server):
                    return False
                self._client.update_note_metadata(server, is_pinned=is_pinned,
                                                  sort_value=sort_value, new_color=new_color,
                                                  new_title=submitted.title)
                self.baselines[note.id] = snapshot(submitted)
                self._finish_write()
                return True
            except Exception:
                self._pause()
                return False

    def create_note(self, title="", text="", color_hex="#FFF475", **kwargs):
        with self._lock:
            if not self.writes_enabled or not self.is_authenticated or self._create_pending:
                return None
            try:
                self._save_recovery(local=snapshot(KeepNote(
                    id="new", title=title, text=text, color_hex=color_hex,
                    is_list=kwargs.get("is_list", False),
                    list_items=deepcopy(kwargs.get("list_items") or []))), reason="create")
                self._start_write("create-in-flight")
                note = super().create_note(title, text, color_hex, **kwargs)
                if note is None:
                    self._pause()
                else:
                    self.baselines[note.id] = snapshot(note)
                    self._create_pending = note.id
                    self._start_write("create-awaiting-local-checkpoint", server_id=note.id)
                return note
            except Exception:
                self._pause()
                return None

    def delete_note(self, note_id):
        with self._lock:
            if not self.writes_enabled or not self.is_authenticated or self._create_pending:
                return False
            try:
                self._client.sync(full=True)
                server = self._client.notes.get(note_id)
                if server is None or server.is_deleted or server.is_trashed:
                    return True
                if self._client.is_snapshot_stale(note_id):
                    raise KeepError("The server snapshot is stale.")
                baseline = self.baselines.get(note_id)
                if baseline is None:
                    raise KeepError("Cannot delete a note without a confirmed baseline.")
                if not self._before_push(note_from_snapshot(baseline), server):
                    return False
                self._client.trash_note(server)
                self._finish_write()
                return True
            except Exception:
                self._pause()
                return False

    def logout(self):
        from config import clear_token
        with self._lock:
            email = self._email
            self._authenticated = False
            self._client = None
            self._email = None
            self.writes_enabled = False
            self.baselines.clear()
            self.conflicts.clear()
            self._create_pending = None
            # Disable in-memory access even if a persistent store is locked.
            clear_token(email)
