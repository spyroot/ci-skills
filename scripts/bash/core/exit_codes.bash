#!/usr/bin/env bash
# shellcheck disable=SC2034 # Public constants are consumed by sourced callers.
# Need move 66 and 64 as dfault
#

[[ "${CI_EXIT_CODES_LOADED:-0}" == 1 ]] && return 0
readonly CI_EXIT_CODES_LOADED=1

readonly CI_EXIT_OK=0
readonly CI_EXIT_FAILURE=1
readonly CI_EXIT_BLOCKED=2
readonly CI_EXIT_USAGE=64
readonly CI_EXIT_INVALID_DATA=65
readonly CI_EXIT_MISSING_FILE=66
readonly CI_EXIT_MISSING_VALUE=67
# What timeout(1) exits with, borrowed the way 64-67 borrow sysexits and 129-143
# borrow 128+signal. It says "did not succeed in time", which is a different
# answer from GALILEO_EXIT_BLOCKED "cannot succeed": a bounded wait that runs out
# has proved nothing about the thing it was waiting for.
readonly CI_EXIT_TIMEOUT=124
readonly CI_EXIT_SIGNAL_HUP=129
readonly CI_EXIT_SIGNAL_INT=130
readonly CI_EXIT_SIGNAL_TERM=143
