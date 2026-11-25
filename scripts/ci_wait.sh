#!/usr/bin/env bash
# Wait for CI on one commit and report it. Exit 0 only if at least one run
# exists for that exact SHA and every run succeeded; "no run found" is a failure.
# Usage: scripts/ci_wait.sh [commit] (default HEAD)
set -uo pipefail
sha="$(git rev-parse "${1:-HEAD}")" || exit 1
appear_s="${CI_APPEAR_S:-300}"; poll_s=15; waited=0

while :; do
  runs="$(gh run list --commit "$sha" --json databaseId,status,conclusion,workflowName,headBranch \
          --jq '.[] | "\(.databaseId) \(.status) \(.conclusion) \(.workflowName) (\(.headBranch))"')" || {
    echo "FAIL: gh run list failed for $sha" >&2; exit 1; }
  if [[ -z "$runs" ]]; then
    if (( waited >= appear_s )); then
      echo "FAIL: no CI run found for $sha after ${appear_s}s" >&2; exit 1
    fi
  elif ! grep -qv " completed " <<<"$runs"; then
    break
  fi
  sleep "$poll_s"; waited=$((waited + poll_s))
done

echo "CI for $sha:"; echo "$runs"
if grep -qv " completed success " <<<"$runs"; then echo "FAIL: not every run succeeded" >&2; exit 1; fi
echo "PASS: all runs succeeded"
