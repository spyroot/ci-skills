#!/usr/bin/env bash
# @TODO Need check portability and pair with tests
#
# Mustafa Bayramov mbyaramo@cisco.com


[[ "${BASIC_AUTH_LOADED:-0}" == 1 ]] && return 0
readonly BASIC_AUTH_LOADED=1

# Write an HTTP Basic Authorization header to a file only this user can read.
#
# ARGS: output file, username, password.
# Exit classes: usage, or failure when the credential cannot be encoded or the
#   file cannot be written.
#
# Shared - core because more than one caller needs it:
# A file, not curl -u: -u puts the password in argv, where any process running
# as this user reads it out of the process table.
ci_basic_auth_header() {
	(($# == 3)) || return "${CI_EXIT_USAGE:-64}"
	local encoded
	encoded="$(printf '%s:%s' "$2" "$3" | base64)" ||
		return "${CI_EXIT_FAILURE:-1}"

	encoded="${encoded//$'\n'/}"
	[[ -n "${encoded}" ]] || return "${CI_EXIT_FAILURE:-1}"
	(umask 077 && printf 'Authorization: Basic %s\n' "${encoded}" >"$1")
}
