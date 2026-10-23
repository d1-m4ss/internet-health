# Third-party notices

## NetProbe_Python (upstream application) — MIT

`src/app.py` is a modified copy of `probe/app.py` from
[bmartino1/NetProbe_Python](https://github.com/bmartino1/NetProbe_Python). The unmodified file is kept in
`upstream/app.py`, and every change is visible in `src/app.py.patch`. The upstream license:

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

This repository does not ship the upstream Docker image; it is pulled by the user from Docker Hub
(`bmmbmm01/netprobe`).

## Dependencies that are used but not bundled

- **speedtest-cli** (Python backend for the optional Speedtest) — Apache-2.0, installed in the upstream image.
- **Ookla Speedtest CLI** (optional backend of the upstream image) — proprietary, **not** covered by this
  repository's license; users accept Ookla's terms themselves.
- **Flask**, **dnspython** — BSD-3-Clause / ISC, installed in the upstream image and via `requirements.txt` for tests.
- **Playwright** (dev only, browser tests) — Apache-2.0.

The web UI (`src/index.html`) is self-contained: no external scripts, fonts or CDNs are loaded.
