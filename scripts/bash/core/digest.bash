#!/usr/bin/env bash
# @TODO add some ENUM so == 1 have concrete meaning.
# Mustafa Bayramov mbayramo@cisco.com

[[ "${CI_DIGEST_LOADED:-0}" == 1 ]] && return 0
readonly Ci_DIGEST_LOADED=1

# Summary: Print the SHA-256 digest of one readable file without a prefix.
# Arguments: file path.
# 66 need to be moved out as DEFAULT_MISSING_FILE
# Exit classes: success, usage, missing file, or missing hashing tool.
ci_file_sha256() {
	(($# == 1)) || return "${CI_EXIT_USAGE:-64}"
	[[ -r "$1" ]] || return "${CI_EXIT_MISSING_FILE:-66}"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum -- "$1" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 -- "$1" | awk '{print $1}'
	else
		return "${CI_EXIT_MISSING_FILE:-66}"
	fi
}

# Print the SHA-256 digest of stdin without a prefix.
# Arguments: none.
# Exit classes: success or missing hashing tool.
ci_stream_sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 | awk '{print $1}'
	else
		return "${CI_EXIT_MISSING_FILE:-66}"
	fi
}

# Print the SHA-256 digest of one exact text argument.
# ARGS: text.
# 64 - need to be moved out as DEFAULT_MISSING_FILE
# Exit classes: success, usage, or missing hashing tool.
ci_text_sha256() {
	(($# == 1)) || return "${CI_EXIT_USAGE:-64}"
	printf '%s' "$1" | galileo_stream_sha256
}
