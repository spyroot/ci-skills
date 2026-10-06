#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
# ci-skills-bless-v1
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source=lib/bash/core/runtime.bash
source "$root/lib/bash/core/runtime.bash"
ci_runtime_require_bash5 "$0" "$@"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# shellcheck source=lib/bash/automation/bless.bash
source "$root/lib/bash/automation/bless.bash"
ci_bless_main "$root" "$@"
