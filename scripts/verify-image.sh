#!/bin/bash
# Checks that the NetProbe image the override files were written for has not changed.
# Usage: verify-image.sh [image]   (defaults to the pinned digest)
# Exit code 0: the hashes of the image's original files match the expected ones; 1 otherwise.
set -euo pipefail

IMAGE="${1:-bmmbmm01/netprobe@sha256:386bdc2c177b11aef4a1223dfd91ae47fe2f9804c5449c5f4c2caae1d048c155}"
EXPECTED_APP=740261cbea96d302daf39edb76aabbfdbea2f216f43fd90be1dd2b33cca2d846
EXPECTED_INDEX=73131088d5cc46e7679575473c6381eebb91bdc9125ee865b53ed523ac54bf74

# Read the original files FROM THE IMAGE, not from a running container (where they are overridden).
actual=$(docker run --rm --pull=never --network none --entrypoint sh "$IMAGE" -c \
    'cd /app && sha256sum app.py templates/index.html') || { echo "cannot read image $IMAGE" >&2; exit 1; }
app=$(awk '$2=="app.py"{print $1}' <<< "$actual")
index=$(awk '$2=="templates/index.html"{print $1}' <<< "$actual")

status=0
[[ "$app" == "$EXPECTED_APP" ]] && echo "app.py: OK" || { echo "app.py: MISMATCH ($app)"; status=1; }
[[ "$index" == "$EXPECTED_INDEX" ]] && echo "templates/index.html: OK" || { echo "templates/index.html: MISMATCH ($index)"; status=1; }
[[ $status -eq 0 ]] || echo "The image changed: do not use the override files until the patch is ported (see docs/INSTALL-UNRAID.md)." >&2
exit $status
