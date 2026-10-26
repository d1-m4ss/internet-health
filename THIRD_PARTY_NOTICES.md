# Third-party notices

## NetProbe_Python (upstream application) — MIT

`src/app.py` is a modified copy of `probe/app.py` from
[bmartino1/NetProbe_Python](https://github.com/bmartino1/NetProbe_Python). The unmodified file is kept in
`upstream/app.py` (the file has been developed on its own since 1.3.0; `diff -u upstream/app.py src/app.py` shows the differences). The upstream license:

```
MIT License

Copyright (c) 2025-2026 Brandon Martino

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

This repository does not use the upstream Docker image: the Internet Health image is built from this repository's `Dockerfile`.

## Dependencies that are used but not bundled

- **speedtest-cli** — used by the upstream application and removed from this project in 1.3.0 (the project was archived in April 2026).
- **Ookla Speedtest CLI** (optional service) — proprietary, **not** covered by this repository's license and **not** in the image: the first Ookla test
  downloads it from Ookla (install.speedtest.net) into the user's data folder; choosing the service in the UI is the user's acceptance of Ookla's terms.
- **Flask**, **dnspython** — BSD-3-Clause / ISC, installed in the upstream image and via `requirements.txt` for tests.
- **Playwright** (dev only, browser tests) — Apache-2.0.

The web UI (`src/index.html`) is self-contained: no external scripts, fonts or CDNs are loaded.
