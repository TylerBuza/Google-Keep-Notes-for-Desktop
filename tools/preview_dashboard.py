"""Capture the native dashboard with fictional notes and isolated app data only."""

import argparse
import os
from pathlib import Path
import sys
import tempfile


def fictional_notes():
    """Shared demo content only; never load an installed user's note cache."""
    from note_model import KeepNote
    return [
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1180)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--editor", choices=("text", "checklist", "short", "long"))
    parser.add_argument("--two-way", action="store_true", help="Show fictional sync-on UI; never connects")
    parser.add_argument("--card", action="store_true", help="Capture a fictional card to compare typography")
    parser.add_argument("--floating", action="store_true", help="Capture a fictional desktop note")
    parser.add_argument("--view", choices=("notes", "archive", "label"), default="notes")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="keep-dashboard-") as data:
        os.environ["APPDATA"] = data
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from PySide6.QtWidgets import QApplication
        from note_model import KeepNote
        from note_manager import NoteManagerDialog

        app = QApplication([])
        samples = fictional_notes()
        if args.view != "notes":
            samples.extend([
                KeepNote(id="demo-archive-1", title="Last season's ideas", labels=["Ideas"],
                         archived=True, color_hex="#D7AEFB", text="Good thoughts, kept for another day."),
                KeepNote(id="demo-archive-2", title="Finished project", labels=["Projects"],
                         archived=True, color_hex="#AECBFA", text="Sketches, plans, and lessons worth keeping."),
            ])
        notes = {note.id: note for note in samples}
        dashboard = NoteManagerDialog(notes, {"demo-1": True}, cloud_labels=["Ideas", "Projects", "Someday"])
        dashboard.set_sync_state(True, args.two_way)
        dashboard.resize(args.width, args.height)
        dashboard.show()
        if args.view != "notes":
            dashboard._set_view("archive" if args.view == "archive" else "label:Ideas")
        target = dashboard
        editor = None
        floating = None
        if args.editor:
            sample = samples[1] if args.editor == "checklist" else samples[0]
            if args.editor in ("short", "long"):
                sample = KeepNote(id="demo-sizing", title="Quick thought", color_hex="#AECBFA",
                                  text="One small idea." if args.editor == "short" else "A fictional line.\n" * 60)
            editor = dashboard.create_editor(sample)
            editor.save_requested.connect(lambda changes: editor.accept())
            dashboard.show_editor()
        if args.floating:
            from note_window import NoteWindow
            floating = NoteWindow("demo-floating", title="Weekend inspiration",
                                  text="A little space for a good idea.", color_hex="#D7AEFB",
                                  dark_mode=True, pinned=True)
            floating.show()
            target = floating
        for _ in range(8):
            app.processEvents()
        if args.card:
            target = dashboard._cards["demo-1"]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not target.grab().save(str(args.output)):
            raise RuntimeError("Could not save preview")
        print(str(args.output))
        if editor is not None:
            editor.accept()
        if floating is not None:
            floating.close()
        dashboard.close()
        app.processEvents()


if __name__ == "__main__":
    main()
