# Contributing

1. `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && npm install`
2. Write the failing test first (`tests/`), then the change.
3. Run everything before opening a pull request:
   ```sh
   python -m unittest discover -s tests
   python tests/simulate_network.py
   CHROME_PATH=/path/to/chrome node tests/test_ui.cjs     # or: npx playwright install chromium
   CHROME_PATH=/path/to/chrome node tests/test_mobile_nav.cjs
   CHROME_PATH=/path/to/chrome node tests/test_speedtest_ui.cjs
   ```
4. `src/app.py` is this project's own source now (the original it started from stays in `upstream/` for attribution only); there is no patch to keep in sync.
   Python packages are pinned in `requirements.txt`: change a version there and run the whole suite.
5. Screenshots in `docs/screenshots` come from `node tests/screenshots.cjs` (invented data, English UI). Never add
   screenshots or fixtures taken from a real network: no real IP addresses, hostnames, router names or Speedtest servers.

## Releasing

Versions follow [semver](https://semver.org): `1.2.3`. The version lives in one place, `VERSION` in `src/netprobe_extensions.py`
(shown by `GET /api/about` and in the page footer); between releases it reads `X.Y.Z-dev`. Everything that changes for users goes
under `## Unreleased` in the CHANGELOG as you work.

1. Wait for a green CI on `main`.
2. `python scripts/release.py release 1.2.3 --dry-run` shows the plan; without `--dry-run` it runs the unit tests, sets the version,
   turns *Unreleased* into `## 1.2.3 — <date>`, commits `Release 1.2.3`, tags `v1.2.3` and starts `1.2.4-dev` in a second commit.
3. `git push origin main v1.2.3` — the tag runs `.github/workflows/release.yml`: it checks that tag, code version and CHANGELOG agree, runs
   the tests, builds the image, checks the pinned base, starts the image on an empty folder, publishes
   `ghcr.io/<owner>/<repo>:1.2.3`, `:1.2` and `:latest`, and creates the GitHub Release with the CHANGELOG notes.
4. Check the package page once: the image should be public (it inherits the repository's visibility through the source label).

Guidelines: the UI stays one self-contained file with no external requests; user-visible text goes through the
`I18N` table (Russian **and** English); never invent data — missing measurements are gaps, not zeros; notification
text must stay free of control characters and backslashes (Unraid agents interpret `\n` escapes).
