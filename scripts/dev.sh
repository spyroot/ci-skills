#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail
if ((BASH_VERSINFO[0] < 5)); then
	if [[ -x /opt/homebrew/bin/bash ]]; then
		exec /opt/homebrew/bin/bash "$0" "$@"
	fi
	printf 'BLOCKER: scripts/dev.sh requires Bash 5 or newer.\n' >&2
	printf 'SAFE_NEXT_STEP: Use the Bash path reported by agent-tools.sh.\n' >&2
	exit 69
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# shellcheck source=scripts/bash/core/dev.bash
source "$root/scripts/bash/core/dev.bash"
ci_dev_main "$root" "$@"
