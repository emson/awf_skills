#!/usr/bin/env bash
# awf-skills install / uninstall.
#
# Usage:
#   ./install.sh             # symlink each skills/awf-* into ~/.claude/skills/
#   ./install.sh --uninstall # remove the symlinks (leaves AWF_HOME export alone)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${HOME}/.claude/skills"

uninstall() {
    if [ ! -d "$TARGET" ]; then
        echo "no $TARGET; nothing to uninstall."
        exit 0
    fi
    local removed=0
    for link in "$TARGET"/awf-*; do
        [ -L "$link" ] || continue
        local dest
        dest="$(readlink "$link")"
        if [[ "$dest" == "$REPO/skills/"* ]]; then
            rm "$link"
            echo "removed: $link"
            removed=$((removed + 1))
        fi
    done
    echo "removed $removed symlink(s). \$AWF_HOME export (if any) left alone."
}

install() {
    mkdir -p "$TARGET"

    if [ ! -d "$REPO/skills" ]; then
        echo "error: $REPO/skills not found." >&2
        exit 1
    fi

    local installed=0
    for skill in "$REPO"/skills/awf-*; do
        [ -d "$skill" ] || continue
        local name
        name="$(basename "$skill")"
        local link="$TARGET/$name"

        if [ -L "$link" ] || [ -e "$link" ]; then
            local existing
            existing="$(readlink "$link" 2>/dev/null || echo '')"
            if [ "$existing" = "$skill" ]; then
                echo "  [ok]    $name (already linked)"
                continue
            fi
            echo "  [skip]  $name (target exists and is not our symlink: $link)"
            continue
        fi

        ln -s "$skill" "$link"
        echo "  [link]  $name -> $skill"
        installed=$((installed + 1))
    done

    echo
    echo "installed $installed skill(s) into $TARGET"
    echo
    echo "next steps:"
    echo "  1. export AWF_HOME=$REPO  (so the awf-init skill can find this checkout)"
    echo "  2. in any Claude Code session: run 'awf-init' — it will create"
    echo "     ~/.config/awf/.env, prompt for missing credentials, and offer"
    echo "     to persist AWF_HOME in your shell rc."
    echo "  3. run 'awf-doctor' to validate."
}

case "${1:-}" in
    --uninstall|-u) uninstall ;;
    "")             install ;;
    *)              echo "usage: $0 [--uninstall]" >&2; exit 2 ;;
esac
