#!/usr/bin/env bash
# Copy the shared Bash runtime into the installable skill package.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
source_file=$repo_root/lib/bash/core/runtime.bash
package_file=$repo_root/ci-skills/lib/bash/core/runtime.bash

# shellcheck source=lib/bash/core/runtime.bash
source "$source_file"

# Summary: Print the runtime copy command's supported actions.
# Stdout: usage text.
# Returns: 0.
usage() {
  printf '%s\n' \
    'Summary: Check or update the installable Bash runtime copy.' \
    'Options:' \
    '  --check                    Compare package and root runtime (default).' \
    '  --plan                     Print a fingerprint of source and destination bytes.' \
    '  --apply --confirm-sync HASH  Apply that exact plan and verify the copy.' \
    '  --help                     Show this help.' \
    'Usage: scripts/sync-bash-runtime.sh [--check|--plan|--apply --confirm-sync HASH|--help]'
}

# Summary: Fingerprint the current root runtime and package copy as one plan.
# Stdout: SHA-256 fingerprint bound to both paths and their current bytes.
# Stderr: hash or file diagnostics.
# Returns: 0 or the underlying read/hash failure.
sync_runtime_plan() {
  local source_hash package_hash
  source_hash=$(ci_sha256_stdin <"$source_file") || return
  if [[ -f $package_file ]]; then
    package_hash=$(ci_sha256_stdin <"$package_file") || return
  else
    package_hash=missing
  fi
  printf '%s\0%s\0%s\0%s\0' \
    "$source_file" "$source_hash" "$package_file" "$package_hash" |
    ci_sha256_stdin
}

# Summary: Atomically replace the package copy after a matching plan is confirmed.
# Arguments: $1 fingerprint printed by --plan.
# Stdout: updated, verified, or current status.
# Stderr: mismatch or copy diagnostic.
# Returns: 0, CI_EXIT_DATA on a changed plan, or CI_EXIT_BLOCKED on I/O failure.
sync_runtime_apply() (
  local expected=$1 actual temp
  actual=$(sync_runtime_plan) || return "$CI_EXIT_BLOCKED"
  if [[ $actual != "$expected" ]]; then
    ci_fail "$CI_EXIT_DATA" 'runtime sync plan changed' \
      'Run --plan again and confirm its current fingerprint.'
    return $?
  fi
  if cmp -s "$source_file" "$package_file"; then
    printf 'Bash runtime copy: current\n'
    return 0
  fi
  temp=$(mktemp "${package_file%/*}/.runtime.bash.XXXXXX") ||
    ci_fail "$CI_EXIT_BLOCKED" 'cannot stage Bash runtime copy' \
      'Check write access to the package runtime directory.' || return $?
  trap 'rm -f -- "$temp"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  cp "$source_file" "$temp" || return "$CI_EXIT_BLOCKED"
  cmp -s "$source_file" "$temp" || return "$CI_EXIT_BLOCKED"
  chmod 0644 "$temp" || return "$CI_EXIT_BLOCKED"
  actual=$(sync_runtime_plan) || return "$CI_EXIT_BLOCKED"
  if [[ $actual != "$expected" ]]; then
    ci_fail "$CI_EXIT_DATA" 'runtime sync plan changed during copy' \
      'Run --plan again and confirm its current fingerprint.'
    return $?
  fi
  mv -f "$temp" "$package_file" || return "$CI_EXIT_BLOCKED"
  cmp -s "$source_file" "$package_file" || return "$CI_EXIT_BLOCKED"
  printf 'Bash runtime copy: updated and verified\n'
)

# Summary: Dispatch a read-only check, a plan, or a confirmed atomic copy.
# Arguments: --check, --plan, --apply --confirm-sync HASH, or --help.
# Stdout: status, fingerprint, or usage.
# Stderr: errors and invalid usage.
# Returns: 0, CI_EXIT_USAGE, CI_EXIT_DATA, or CI_EXIT_BLOCKED.
main() {
  case ${1:---check} in
  --help)
    (($# == 1)) || return "$CI_EXIT_USAGE"
    usage
    ;;
  --check)
    (($# <= 1)) || return "$CI_EXIT_USAGE"
    if cmp -s "$source_file" "$package_file"; then
      printf 'Bash runtime copy: current\n'
    else
      printf 'Bash runtime copy differs; run --plan before --apply.\n' >&2
      return 1
    fi
    ;;
  --plan)
    (($# == 1)) || return "$CI_EXIT_USAGE"
    sync_runtime_plan
    ;;
  --apply)
    if (($# != 3)) || [[ $2 != --confirm-sync || ! $3 =~ ^[0-9a-f]{64}$ ]]; then
      usage >&2
      return "$CI_EXIT_USAGE"
    fi
    sync_runtime_apply "$3"
    ;;
  *)
    usage >&2
    return "$CI_EXIT_USAGE"
    ;;
  esac
}

main "$@"
