#!/usr/bin/env bash
# Resolve Bash 5 for the repository's source graph checks.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

# shellcheck source=lib/bash/core/runtime.bash
source "${BASH_SOURCE[0]%/*}/runtime.bash"

# Summary: Find an installed Bash 5 or newer for repository checks.
# Stdout: absolute interpreter path.
# Stderr: blocker and next step when no supported Bash exists.
# Returns: 0 or CI_EXIT_BLOCKED.
ci_bash5_resolve() {
  local candidate
  for candidate in "$(command -v bash)" /opt/homebrew/bin/bash /usr/local/bin/bash; do
    [[ -n $candidate && -x $candidate ]] || continue
    if "$candidate" -c '((BASH_VERSINFO[0] >= 5))' </dev/null 2>/dev/null; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  ci_fail "$CI_EXIT_BLOCKED" 'bless needs Bash 5 or newer' \
    'Install Bash 5 with the repository toolchain, then retry.'
}
