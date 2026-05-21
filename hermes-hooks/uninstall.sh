#!/usr/bin/env bash
# Reverse install.sh: remove the hook script and the marker-delimited
# config block from ~/.hermes/config.yaml.
set -euo pipefail

HOOKS_DEST_DIR="$HOME/.hermes/agent-hooks"
HOOK_FILE="deny-list-file-access.py"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
MARKER_BEGIN="# >>> personal-ai-stack:file-acl-hook >>>"
MARKER_END="# <<< personal-ai-stack:file-acl-hook <<<"

if [ -f "$HOOKS_DEST_DIR/$HOOK_FILE" ]; then
    rm -f "$HOOKS_DEST_DIR/$HOOK_FILE"
    echo "Removed: $HOOKS_DEST_DIR/$HOOK_FILE"
fi

if [ -f "$HERMES_CONFIG" ] && grep -q "$MARKER_BEGIN" "$HERMES_CONFIG"; then
    # Strip the block between (and including) the markers.
    python3 - "$HERMES_CONFIG" "$MARKER_BEGIN" "$MARKER_END" <<'PY'
import re
import sys
path, begin, end = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
pattern = r"\n?" + re.escape(begin) + r".*?" + re.escape(end) + r"\n?"
new = re.sub(pattern, "\n", text, flags=re.DOTALL)
open(path, "w").write(new)
print(f"Removed marker block from {path}")
PY
fi

echo "Uninstall complete."
