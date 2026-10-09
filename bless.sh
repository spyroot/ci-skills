#!/usr/bin/env bash
# The repository's lint entrypoint checks staged, changed, or working-tree bytes.
# .githooks/pre-commit calls it with --staged; CI calls it with --base REF. The
# checks live in lib/bash/automation/bless.bash.
#
# Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail

BLESS_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
readonly BLESS_ROOT

# shellcheck source=lib/bash/core/bash_runtime.bash
source "$BLESS_ROOT/lib/bash/core/bash_runtime.bash"
if ((BASH_VERSINFO[0] < 5)); then
  BLESS_BASH5=$(ci_bash5_resolve) || exit "$CI_EXIT_BLOCKED"
  exec "$BLESS_BASH5" "$0" "$@"
fi

# shellcheck source=lib/bash/automation/bless.bash
source "$BLESS_ROOT/lib/bash/automation/bless.bash"

# Summary: Print the command's accepted scopes, options, and exit classes.
# Stdout: human-readable help.
# Returns: 0.
usage() {
  printf '%s\n' \
    'Summary: Check staged, committed-delta, or nonignored working files.' \
    'Examples:' \
    '  Check staged files before commit: ./bless.sh --staged' \
    '  Check files changed from a Git revision: ./bless.sh --base REF' \
    '  Check all working files: ./bless.sh --all' \
    'Options:' \
    '  --staged             Check the index (default).' \
    '  --base REF           Check committed files changed from REF through HEAD.' \
    '  --all                Check tracked and nonignored untracked working files.' \
    '  --dry-run            List selected paths and checks without running them.' \
    '  --log-format FORMAT  Log as text or json (default: text).' \
    '  --log-level LEVEL    Minimum log level: debug, info, warning, error.' \
    '  --log-file PATH      Also write log lines to a file.' \
    '  --run-id ID          Include this identifier in JSON logs.' \
    '  --help               Show this help.' \
    'Output modes:' \
    '  text                 Human-readable check results; text logs by default.' \
    '  json                 JSON Lines logs with --log-format json.' \
    'Usage: ./bless.sh [--staged|--base REF|--all] [--dry-run] [logging options]' \
    'Exit: 0 pass, 1 check failed, 64 usage, 69 blocked.'
}

# Summary: Select one check scope and dispatch the repository blessing.
# Arguments: $@: scope, dry run, logging options, or help.
# Stdout: help, plan, or check results.
# Stderr: usage and check failures.
# Returns: check status or CI_EXIT_USAGE for invalid options.
main() {
  local dry_run=false scope=staged selected=false log_enabled=false status=0 base=''
  local value
  CI_LOG_FORMAT=${CI_LOG_FORMAT:-text}
  CI_LOG_LEVEL=${CI_LOG_LEVEL:-info}
  CI_LOG_FILE=${CI_LOG_FILE:-}
  CI_RUN_ID=${CI_RUN_ID:-}
  while (($# > 0)); do
    case $1 in
    --staged | --all)
      if [[ $selected == true ]]; then
        usage >&2
        return "$CI_EXIT_USAGE"
      fi
      scope=${1#--}
      selected=true
      ;;
    --base)
      if [[ $selected == true ]] || (($# < 2)) || [[ -z $2 || $2 == --* ]]; then
        usage >&2
        return "$CI_EXIT_USAGE"
      fi
      scope=changed
      base=$2
      selected=true
      shift
      ;;
    --dry-run) dry_run=true ;;
    --log-format | --log-level | --log-file | --run-id)
      if (($# < 2)) || [[ -z $2 || $2 == --* ]]; then
        usage >&2
        return "$CI_EXIT_USAGE"
      fi
      value=$2
      case $1 in
      --log-format) CI_LOG_FORMAT=$value ;;
      --log-level) CI_LOG_LEVEL=$value ;;
      --log-file) CI_LOG_FILE=$value ;;
      --run-id) CI_RUN_ID=$value ;;
      esac
      log_enabled=true
      shift
      ;;
    --help)
      usage
      return 0
      ;;
    *)
      usage >&2
      return "$CI_EXIT_USAGE"
      ;;
    esac
    shift
  done
  if [[ $CI_LOG_FORMAT != text && $CI_LOG_FORMAT != json ]] ||
    [[ ! $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]]; then
    usage >&2
    return "$CI_EXIT_USAGE"
  fi
  if [[ $log_enabled == true ]]; then
    ci_log info bless start "$scope" ||
      ci_fail "$CI_EXIT_BLOCKED" 'cannot write bless log' 'Check the log path and jq installation.' ||
      return
  fi
  bless_run "$BLESS_ROOT" "$scope" "$dry_run" "$base" || status=$?
  if [[ $log_enabled == true ]]; then
    ci_log info bless finish "status=$status" ||
      ci_fail "$CI_EXIT_BLOCKED" 'cannot write bless log' 'Check the log path and jq installation.' ||
      return
  fi
  return "$status"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
