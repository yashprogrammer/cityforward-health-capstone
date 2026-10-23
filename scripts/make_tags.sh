#!/usr/bin/env bash
# The authoring environment could not push git tags (its git proxy rejects them), so every build session was pushed as a branch `session/<tag>`.
# Run this once from a machine that can push tags; it turns each session branch into the tag of the same name and pushes the tags.
#   scripts/make_tags.sh            create and push
#   scripts/make_tags.sh --dry-run  show what it would do
set -euo pipefail
git fetch origin --prune
for ref in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin/session/); do
  tag="${ref#origin/session/}"
  sha="$(git rev-parse "$ref")"
  if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then echo "exists: $tag"; continue; fi
  echo "tag $tag -> ${sha:0:9}  $(git log -1 --format=%s "$sha" | cut -c1-70)"
  [[ "${1:-}" == "--dry-run" ]] || git tag "$tag" "$sha"
done
[[ "${1:-}" == "--dry-run" ]] || git push origin --tags
