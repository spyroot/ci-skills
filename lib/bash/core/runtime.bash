#!/usr/bin/env bash
# Development Bash runtime shared by bless, dev, and the hook installer.
# shellcheck source=ci-skills/lib/bash/core/runtime.bash
source "${BASH_SOURCE[0]%/*}/../../../ci-skills/lib/bash/core/runtime.bash"

[[ ${CI_DEV_RUNTIME_LOADED:-0} == 1 ]] && return 0
CI_DEV_RUNTIME_LOADED=1

CI_EXIT_FAILED=1
CI_EXIT_CONFLICT=73
CI_EXIT_SIGNAL_HUP=129
CI_EXIT_SIGNAL_INT=130
CI_EXIT_SIGNAL_TERM=143

# Summary: Re-execute a thin entrypoint with an available Bash 5 runtime.
# Arguments: entrypoint path, then its original arguments.
# Returns: 0 under Bash 5; blocked status when none is available.
ci_runtime_require_bash5() {
	local entrypoint=$1 candidate path_candidate=''
	shift
	if ((BASH_VERSINFO[0] >= 5)); then return 0; fi
	path_candidate="$(command -v bash || true)"
	for candidate in "$path_candidate" /opt/homebrew/bin/bash /usr/local/bin/bash /usr/bin/bash; do
		if [[ -x $candidate ]] && "$candidate" -c '((BASH_VERSINFO[0] >= 5))' 2>/dev/null; then
			exec "$candidate" "$entrypoint" "$@"
		fi
	done
	ci_fail "$CI_EXIT_BLOCKED" 'Bash 5 or newer is unavailable' \
		'Use the Bash path reported by agent-tools.sh.'
}

# Summary: Exit with the shared signal status so EXIT cleanup can run.
# Arguments: HUP, INT, or TERM.
ci_runtime_signal_exit() {
	case $1 in
	HUP) exit "$CI_EXIT_SIGNAL_HUP" ;;
	INT) exit "$CI_EXIT_SIGNAL_INT" ;;
	TERM) exit "$CI_EXIT_SIGNAL_TERM" ;;
	*) return "$CI_EXIT_USAGE" ;;
	esac
}

# Summary: Initialize one process-owned LIFO cleanup stack and lifecycle traps.
# Side effects: installs the shared ERR, EXIT, HUP, INT, and TERM handlers.
# Idempotency: a second call keeps the current stack and traps.
ci_runtime_lifecycle_begin() {
	if [[ ${CI_RUNTIME_LIFECYCLE_ACTIVE:-0} == 1 ]]; then return 0; fi
	CI_RUNTIME_LIFECYCLE_ACTIVE=1
	CI_RUNTIME_CLEANUP_FUNCTIONS=()
	CI_RUNTIME_CLEANUP_ARGUMENTS=()
	CI_RUNTIME_CLEANUP_RAN=0
	CI_RUNTIME_CLEANUP_STATUS=0
	CI_RUNTIME_ERR_STATUS=0
	trap 'ci_runtime_error "$?"' ERR
	trap 'ci_runtime_exit "$?"' EXIT
	trap 'ci_runtime_signal_exit HUP' HUP
	trap 'ci_runtime_signal_exit INT' INT
	trap 'ci_runtime_signal_exit TERM' TERM
}

# Summary: Register one existing cleanup function and its single resource argument.
# Arguments: cleanup function name, resource identifier.
# Returns: 0 or usage status for an invalid registration.
ci_runtime_cleanup_push() {
	[[ ${CI_RUNTIME_LIFECYCLE_ACTIVE:-0} == 1 && $# == 2 ]] || return "$CI_EXIT_USAGE"
	declare -F "$1" >/dev/null || return "$CI_EXIT_USAGE"
	CI_RUNTIME_CLEANUP_FUNCTIONS+=("$1")
	CI_RUNTIME_CLEANUP_ARGUMENTS+=("$2")
}

# Summary: Remove one owned temporary file and prove its absence.
# Arguments: nonempty file path.
ci_runtime_cleanup_file() {
	[[ -n $1 ]] || return "$CI_EXIT_USAGE"
	rm -f -- "$1" || return "$CI_EXIT_FAILED"
	[[ ! -e $1 && ! -L $1 ]]
}

# Summary: Remove one owned temporary directory and prove its absence.
# Arguments: nonempty directory path.
ci_runtime_cleanup_dir() {
	[[ -n $1 && $1 != / ]] || return "$CI_EXIT_USAGE"
	rm -rf -- "$1" || return "$CI_EXIT_FAILED"
	[[ ! -e $1 && ! -L $1 ]]
}

# Summary: Run registered cleanup actions in reverse order and record each result.
# Stderr: one bounded PASS or FAIL line per action, without resource contents.
# Returns: 1 if any action fails, including on a repeated call.
ci_runtime_cleanup_run() {
	local index failed=0 function
	if [[ ${CI_RUNTIME_CLEANUP_RAN:-0} == 1 ]]; then
		return "$CI_RUNTIME_CLEANUP_STATUS"
	fi
	CI_RUNTIME_CLEANUP_RAN=1
	for ((index = ${#CI_RUNTIME_CLEANUP_FUNCTIONS[@]} - 1; index >= 0; index--)); do
		function="${CI_RUNTIME_CLEANUP_FUNCTIONS[$index]}"
		if "$function" "${CI_RUNTIME_CLEANUP_ARGUMENTS[$index]}"; then
			printf 'CLEANUP_PASS: %s\n' "$function" >&2
		else
			printf 'CLEANUP_FAIL: %s\n' "$function" >&2
			failed=1
		fi
	done
	CI_RUNTIME_CLEANUP_STATUS="$failed"
	return "$failed"
}

# Summary: Record an unhandled command error for final lifecycle status.
# Arguments: command exit status.
ci_runtime_error() {
	CI_RUNTIME_ERR_STATUS="$1"
}

# Summary: Run the shared cleanup stack and make incomplete cleanup fatal.
# Arguments: process exit status before cleanup.
ci_runtime_exit() {
	local status="$1"
	trap - ERR EXIT HUP INT TERM
	if [[ "$status" == 0 && ${CI_RUNTIME_ERR_STATUS:-0} != 0 ]]; then
		status="$CI_RUNTIME_ERR_STATUS"
	fi
	if ! ci_runtime_cleanup_run; then
		status="$CI_EXIT_FAILED"
	fi
	exit "$status"
}
