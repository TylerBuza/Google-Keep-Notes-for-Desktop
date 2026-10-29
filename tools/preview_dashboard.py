"""Capture the native dashboard with fictional notes and isolated app data only."""

import argparse
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--editor", choices=("text", "checklist"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="keep-dashboard-") as data:
        os.environ["APPDATA"] = data
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from PySide6.QtWidgets import QApplication
        from note_model import KeepNote
        from note_manager import NoteManagerDialog

        app = QApplication([])
        samples = [
            KeepNote(id="demo-1", title="A little inspiration", pinned=True, labels=["Ideas"],
                     color_hex="#FFFFFF", text="Make room for the things that matter.\n\nOne good idea at a time."),
            KeepNote(id="demo-2", title="This week", pinned=True, color_hex="#AECBFA", is_list=True,
                     list_items=[{"text": "Sketch the next project", "checked": False},
                                 {"text": "Pick up groceries", "checked": False},
                                 {"text": "Clear the desk", "checked": True}]),
            KeepNote(id="demo-3", title="Weekend plans", color_hex="#CCFF90",
                     text="A walk, a good book,\nand absolutely no alarms."),
            KeepNote(id="demo-4", title="", labels=["Ideas"],
                     color_hex="#FFFFFF", text="You don’t have to do it all today."),
            KeepNote(id="demo-5", title="Small improvements", color_hex="#FDCFE8",
                     text="Less clutter.\nMore breathing room.\nA place for every idea."),
            KeepNote(id="demo-6", title="Reading list", color_hex="#E8EAED",
                     text="A thoughtful essay\nA very long novel\nSomething completely different"),
        ]
        notes = {note.id: note for note in samples}
        dashboard = NoteManagerDialog(notes, {"demo-1": True})
        dashboard.set_sync_state(True, False)
        dashboard.resize(args.width, args.height)
        dashboard.show()
        target = dashboard
        editor = None
        if args.editor:
            editor = dashboard.create_editor(samples[1] if args.editor == "checklist" else samples[0])
            editor.save_requested.connect(lambda changes: editor.accept())
            dashboard.show_editor()
        for _ in range(8):
            app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not target.grab().save(str(args.output)):
            raise RuntimeError("Could not save preview")
        print(str(args.output))
        if editor is not None:
            editor.accept()
        dashboard.close()
        app.processEvents()


if __name__ == "__main__":
    main()
