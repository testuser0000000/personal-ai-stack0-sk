#!/usr/bin/env bash
# Install the file-access deny-list hook into ~/.hermes/.
#
# Idempotent: re-running is safe — it copies the latest script and
# only appends the config block if the marker isn't already there.
#
# To uninstall: run ./uninstall.sh in this directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK_FILE="deny-list-file-access.py"
HOOKS_DEST_DIR="$HOME/.hermes/agent-hooks"
HERMES_CONFIG="$HOME/.hermes/config.yaml"
MARKER_BEGIN="# >>> personal-ai-stack:file-acl-hook >>>"
MARKER_END="# <<< personal-ai-stack:file-acl-hook <<<"

if [ ! -f "$HERMES_CONFIG" ]; then
    echo "ERROR: $HERMES_CONFIG not found. Is Hermes installed?"
    exit 1
fi

mkdir -p "$HOOKS_DEST_DIR"
cp "$SCRIPT_DIR/$HOOK_FILE" "$HOOKS_DEST_DIR/$HOOK_FILE"
chmod +x "$HOOKS_DEST_DIR/$HOOK_FILE"
echo "Installed: $HOOKS_DEST_DIR/$HOOK_FILE"

# Append config block only if our marker isn't already in the file.
if grep -q "$MARKER_BEGIN" "$HERMES_CONFIG"; then
    echo "Hook config already present in $HERMES_CONFIG (marker found)."
else
    cat >> "$HERMES_CONFIG" <<EOF

$MARKER_BEGIN
# Deny-list file ACL — blocks read_file / search_files for sensitive
# paths. Installed by hermes-hooks/install.sh in personal-ai-stack0-sk.
hooks:
  pre_tool_call:
    - matcher: "read_file|search_files"
      command: "~/.hermes/agent-hooks/$HOOK_FILE"
      timeout: 5
hooks_auto_accept: true
$MARKER_END
EOF
    echo "Appended hook config to $HERMES_CONFIG"
    echo "  (look for the markers '$MARKER_BEGIN' / '$MARKER_END')"
fi

echo
echo "Test it:"
echo "  hermes hooks test pre_tool_call --for-tool read_file \\"
echo "    --payload-file $SCRIPT_DIR/tests/payload-blocked.json"
echo
echo "Or simply ask Hermes: 'read the file at ~/.ssh/id_rsa'"
echo "It should refuse with the deny-list message."
