#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# shellcheck source=lib/bash/core/runtime.bash
source "$root/lib/bash/core/runtime.bash"
ci_runtime_require_bash5 "$0" "$@"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# shellcheck source=lib/bash/automation/dev.bash
source "$root/lib/bash/automation/dev.bash"
ci_dev_main "$root" "$@"
