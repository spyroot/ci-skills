#!/usr/bin/env bash
# The repository's pre-commit hook: checks the staged bytes before every commit.
# .githooks/pre-commit calls it; `make bless` activates that hook
# (core.hooksPath .githooks) and runs it. The checks live in
# ci-skills/lib/bash/bless/bless.bash.
#
# Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail

BLESS_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly BLESS_ROOT

# shellcheck source=ci-skills/lib/bash/core/runtime.bash
source "$BLESS_ROOT/ci-skills/lib/bash/core/runtime.bash"
# shellcheck source=ci-skills/lib/bash/bless/bless.bash
source "$BLESS_ROOT/ci-skills/lib/bash/bless/bless.bash"

usage() {
  printf '%s\n' \
    'Usage: ./bless.sh [--staged] [--dry-run]' \
    '       ./bless.sh --help' \
    '' \
    'Checks the staged bytes (the index) before a commit:' \
    "  $BLESS_CHECKS" \
    '' \
    '  --staged        check the index (the default; the hook passes it)' \
    '  --dry-run       list the staged paths and the checks; run nothing' \
    '  --help          this text' \
    '' \
    'Install: make bless (activates .githooks/pre-commit, then runs this script).' \
    'Output: one human-readable line per check.' \
    'Exit: 0 pass, 1 a check failed, 64 usage, 69 blocked (a tool or Python is missing).'
}

main() {
  local dry_run=false
  while (($# > 0)); do
    case $1 in
    --staged) ;;
    --dry-run) dry_run=true ;;
    --help)
      usage
      return 0
      ;;
    *)
      usage >&2
      return "$CI_EXIT_USAGE"
      ;;
    esac
    shift
  done
  bless_run "$BLESS_ROOT" "$dry_run"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
