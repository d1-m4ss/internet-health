# Where this project comes from

Internet Health started as a fork of **NetProbe_Python** by Brandon Martino (<https://github.com/bmartino1/NetProbe_Python>, MIT,
Copyright (c) 2025-2026 Brandon Martino; the licence text is in `THIRD_PARTY_NOTICES.md`).

- `upstream/app.py` is the unmodified `probe/app.py` it started from: the file of the Docker image
  `bmmbmm01/netprobe@sha256:386bdc2c177b11aef4a1223dfd91ae47fe2f9804c5449c5f4c2caae1d048c155`
  (Python 3.14, Flask 3.1.3; image created 2026-07-21), sha256 `740261cbea96d302daf39edb76aabbfdbea2f216f43fd90be1dd2b33cca2d846`.
  It is kept for attribution and to compare with: `diff -u upstream/app.py src/app.py`.
- Until 1.2.x the image was that upstream image with three files added on top (and `src/app.py.patch` described the difference).
  Since **1.3.0** the image is built from this repository's own `Dockerfile` and `requirements.txt`; the upstream image is not used and its
  files (`static/`, the original `templates/index.html`, the Postgres mode, the speedtest-cli engine) are not part of the image.
- To see whether the original has fixed something worth taking over, compare its current `probe/app.py` with `upstream/app.py`
  (last looked at 2026-10-26: unchanged since 2026-07-21).
