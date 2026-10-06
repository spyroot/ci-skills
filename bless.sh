#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
# ci-skills-bless-v1
set -Eeuo pipefail
if ((BASH_VERSINFO[0] < 5)); then
	if [[ -x /opt/homebrew/bin/bash ]]; then
		exec /opt/homebrew/bin/bash "$0" "$@"
	fi
	printf 'BLOCKER: bless.sh requires Bash 5 or newer.\n' >&2
	printf 'SAFE_NEXT_STEP: Run make bless with the declared project toolchain.\n' >&2
	exit 69
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
# shellcheck source=scripts/bash/core/bless.bash
source "$root/scripts/bash/core/bless.bash"
ci_bless_main "$root" "$@"
