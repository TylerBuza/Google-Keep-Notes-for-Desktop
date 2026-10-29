# Bundled interface fonts

Unmodified static TrueType fonts from Google Fonts, retrieved on 2026-10-29.
Google Sans is used for note titles/headings; Roboto for note bodies and controls.
Regular/medium title faces and regular/medium/bold/italic/bold-italic body faces
are bundled so rendering does not depend on fonts installed on the user's PC.

These files are licensed under the SIL Open Font License 1.1, not the app's AGPL.
Copyright notices and license are in `OFL.txt`; Google Sans trademark terms are
in `GoogleSans-TRADEMARKS.md`. The app is not affiliated with or endorsed by Google.

`manifest.json` records each original source URL and SHA256. Google Fonts CSS:

https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500&family=Roboto:ital,wght@0,400;0,500;0,700;1,400;1,700&display=swap

License sources:
- https://github.com/google/fonts/blob/main/ofl/googlesans/OFL.txt
- https://github.com/google/fonts/blob/main/ofl/roboto/OFL.txt
- https://github.com/google/fonts/blob/main/ofl/googlesans/TRADEMARKS.md

Qt registers only these local assets for this process; it neither installs fonts
system-wide nor requests any URL above at runtime. CI verifies the packaged font
hashes and license files before producing an installer.
