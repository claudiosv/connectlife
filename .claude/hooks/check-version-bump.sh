#!/usr/bin/env bash
# PreToolUse(Bash) hook: block `git push` unless this push's commits bump
# custom_components/claudio_hisense/manifest.json's version AND add a
# CHANGELOG.md entry. Standing rule requested by the user so HACS/HA update
# dialogs always show what changed.
set -euo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')

# Only act on commands that actually invoke `git push`.
if ! printf '%s' "$cmd" | grep -qE '(^|[;&|]|[[:space:]])git[[:space:]]+push([[:space:]]|$)'; then
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root" || exit 0

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)
if [ -z "$upstream" ]; then
  # No upstream tracked (e.g. first push of a new branch) — nothing to diff.
  exit 0
fi

ahead=$(git rev-list "$upstream"..HEAD --count 2>/dev/null || echo 0)
if [ "$ahead" -eq 0 ]; then
  exit 0
fi

manifest_changed=$(git diff --name-only "$upstream"...HEAD -- custom_components/claudio_hisense/manifest.json)
changelog_changed=$(git diff --name-only "$upstream"...HEAD -- CHANGELOG.md)

if [ -z "$manifest_changed" ] || [ -z "$changelog_changed" ]; then
  reason="Push blocked: bump the version in custom_components/claudio_hisense/manifest.json and add a CHANGELOG.md entry before pushing (standing rule so HACS/HA update dialogs show what changed). Remember: also tag+release afterwards (git tag vX.Y.Z + gh release create) since HACS reads version/changelog from GitHub Releases, not the manifest field alone."
  printf '{"decision":"block","reason":%s}\n' "$(printf '%s' "$reason" | jq -Rs .)"
  exit 0
fi

exit 0
