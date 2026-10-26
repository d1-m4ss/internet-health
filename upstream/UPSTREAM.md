# Pinned upstream

- Project: <https://github.com/bmartino1/NetProbe_Python> (MIT, Copyright (c) 2025-2026 Brandon Martino)
- Image used and tested: `bmmbmm01/netprobe@sha256:386bdc2c177b11aef4a1223dfd91ae47fe2f9804c5449c5f4c2caae1d048c155`
  (Python 3.14, Flask 3.1.3; image created 2026-07-21)
- `app.py` here is the unmodified `/app/app.py` of that image:
  `sha256 740261cbea96d302daf39edb76aabbfdbea2f216f43fd90be1dd2b33cca2d846`
- `templates/index.html` of that image (replaced by `src/index.html`):
  `sha256 73131088d5cc46e7679575473c6381eebb91bdc9125ee865b53ed523ac54bf74`

If your image differs, run `scripts/verify-image.sh <image>`; on a mismatch re-apply `src/app.py.patch`
to the new `app.py` (`patch app.py < src/app.py.patch`) and re-run the tests before deploying.

The `Dockerfile`'s `FROM` line carries this digest, and CI pulls it and runs `scripts/verify-image.sh` on every build and release, so a changed base cannot be
published unnoticed. To move to a newer NetProbe image: change the digest in the `Dockerfile` and in `scripts/verify-image.sh` (with the new file hashes),
port the hooks with `patch`, run the whole test suite, and release a new version.
