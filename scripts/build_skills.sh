#!/usr/bin/env bash
#
# build_skills.sh — package each skill in this repo as an installable .skill.
#
# A .skill file is a plain zip (with a .skill extension) containing ONE
# top-level folder named after the skill. That folder holds the skill's
# SKILL.md plus only the files SKILL.md actually references. We build the file
# list from `git ls-files`, so anything untracked or gitignored — __pycache__,
# data/, .DS_Store, the private profiles/zones.local.json override — is
# excluded automatically. tests/, dist/, and references/ are left out by design
# (references/ docs are big and re-derivable; tests aren't shipped).
#
# Output: dist/inbox-catalog.skill, dist/amazon-tracker.skill,
#         dist/extract-purchase-items.skill
#
# Usage: scripts/build_skills.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="$REPO_ROOT/dist"
mkdir -p "$OUT_DIR"

# Expand a repo path (file or dir) to its tracked files. Directories expand to
# every tracked file beneath them; a plain file expands to itself if tracked.
tracked_files() {
  git ls-files -- "$1"
}

# build_skill <skill-name> <spec>...
# Each spec is "SRC::DEST": SRC is a repo-relative path (file or dir), DEST is
# where it lands inside the skill's top-level folder. A directory SRC copies
# each tracked file beneath it, preserving its subpath under DEST.
build_skill() {
  local name="$1"; shift
  local staging root out
  staging="$(mktemp -d)"
  root="$staging/$name"
  mkdir -p "$root"

  echo ""
  echo "== $name.skill =="
  local count=0

  local spec src dest f rel target
  for spec in "$@"; do
    src="${spec%%::*}"
    dest="${spec##*::}"

    local files
    files="$(tracked_files "$src")"
    if [[ -z "$files" ]]; then
      echo "  !! no tracked files for '$src' — skipped" >&2
      continue
    fi

    while IFS= read -r f; do
      if [[ -d "$src" ]]; then
        rel="${f#"$src"/}"          # path relative to the source dir
        target="$dest/$rel"
      else
        target="$dest"              # single file → exact dest
      fi
      mkdir -p "$root/$(dirname "$target")"
      cp "$f" "$root/$target"
      echo "  + $target"
      count=$((count + 1))
    done <<< "$files"
  done

  out="$OUT_DIR/$name.skill"
  rm -f "$out"
  ( cd "$staging" && zip -q -r -X "$out" "$name" )
  rm -rf "$staging"
  echo "  → $out  ($count files, $(du -h "$out" | cut -f1))"
}

# 1) inbox-catalog — the full engine skill. Ships the inboxcatalog/ package
#    (so `python3 -m inboxcatalog` and the offline --fixtures demo run
#    standalone) plus the mail-access guide it points at.
build_skill "inbox-catalog" \
  "SKILL.md::SKILL.md" \
  "inboxcatalog::inboxcatalog" \
  "docs/connect-gmail.md::docs/connect-gmail.md"

# 2) amazon-tracker — a thin wrapper over the same engine. It locates the
#    engine at runtime, so the bundle is just its SKILL.md and the pointer doc.
build_skill "amazon-tracker" \
  "amazon-tracker/SKILL.md::SKILL.md" \
  "amazon-tracker/docs/connect-gmail.md::docs/connect-gmail.md"

# 3) extract-purchase-items — a pure-instruction extraction skill plus the
#    golden input→output example pairs its SKILL.md references. The test
#    harness (test_examples.py) is intentionally left out.
build_skill "extract-purchase-items" \
  "skills/extract-purchase-items/SKILL.md::SKILL.md" \
  "skills/extract-purchase-items/examples::examples"

echo ""
echo "Done. Built in $OUT_DIR:"
ls -1 "$OUT_DIR"/*.skill
