# AI Desk Chrome Extension (MVP)

1. Open `chrome://extensions`, enable Developer mode, and load this directory
   as an unpacked extension.
2. Copy the generated extension ID.
3. From the repository root on macOS, run:

   `python3 -m application.browser.install_native_host <extension-id>`

4. Restart Chrome and run AI Desk.

The extension runs only on HTTP/HTTPS pages and sends only URL/title/
description/headings/language and media state. It never reads local file URLs,
form values, passwords, cookies, or full HTML. Incognito events are rejected by
the native host. Use the AI Desk pause button to stop collection; disable the
extension to stop browser messages entirely.
