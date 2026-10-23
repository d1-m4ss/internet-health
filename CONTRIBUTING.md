# Contributing

1. `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && npm install`
2. Write the failing test first (`tests/`), then the change.
3. Run everything before opening a pull request:
   ```sh
   python -m unittest discover -s tests
   python tests/simulate_network.py
   CHROME_PATH=/path/to/chrome node tests/test_ui.cjs     # or: npx playwright install chromium
   ```
4. Keep `src/app.py.patch` in sync when you touch `src/app.py`
   (`diff -u upstream/app.py src/app.py > src/app.py.patch`; CI checks that the patch reproduces the file).
5. Screenshots in `docs/screenshots` come from `node tests/screenshots.cjs` (invented data, English UI). Never add
   screenshots or fixtures taken from a real network: no real IP addresses, hostnames, router names or Speedtest servers.

Guidelines: the UI stays one self-contained file with no external requests; user-visible text goes through the
`I18N` table (Russian **and** English); never invent data — missing measurements are gaps, not zeros; notification
text must stay free of control characters and backslashes (Unraid agents interpret `\n` escapes).
