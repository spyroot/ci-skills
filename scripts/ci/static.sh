#!/usr/bin/env bash
# GitHub static checks for committed candidate bytes. The local pre-commit
# entrypoint remains bless.sh; both callers reuse lib/bash/automation/bless.bash.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail

CI_STATIC_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
readonly CI_STATIC_ROOT

# shellcheck source=lib/bash/core/bash_runtime.bash
source "$CI_STATIC_ROOT/lib/bash/core/bash_runtime.bash"
if ((BASH_VERSINFO[0] < 5)); then
  CI_STATIC_BASH5=$(ci_bash5_resolve) || exit "$CI_EXIT_BLOCKED"
  exec "$CI_STATIC_BASH5" "$0" "$@"
fi

# shellcheck source=lib/bash/automation/bless.bash
source "$CI_STATIC_ROOT/lib/bash/automation/bless.bash"

usage() {
  printf '%s\n' \
    'Summary: Run required GitHub static checks against a committed candidate.' \
    'Examples:' \
    '  Run candidate checks: scripts/ci/static.sh --base origin/main' \
    '  Show selected paths: scripts/ci/static.sh --base origin/main --dry-run' \
    'Options:' \
    '  --base REF           Compare REF through HEAD.' \
    '  --dry-run            List selected paths and checks without running them.' \
    '  --log-format FORMAT  Log as text or json (default: text).' \
    '  --log-level LEVEL    Minimum log level: debug, info, warning, error.' \
    '  --log-file PATH      Also write log lines to a file.' \
    '  --run-id ID          Include this identifier in diagnostic logs.' \
    '  --help               Show this help.' \
    'Output modes:' \
    '  text                 Human-readable check results; text logs by default.' \
    '  json                 JSON Lines logs with --log-format json.' \
    'Exit: 0 pass, 1 check failed, 64 usage, 69 blocked.'
}

main() {
  local base='' dry_run=false log_enabled=false value status=0
  CI_LOG_FORMAT=${CI_LOG_FORMAT:-text}
  CI_LOG_LEVEL=${CI_LOG_LEVEL:-info}
  CI_LOG_FILE=${CI_LOG_FILE:-}
  CI_RUN_ID=${CI_RUN_ID:-}
  while (($# > 0)); do
    case $1 in
    --base)
      if (($# < 2)) || [[ -z $2 || $2 == --* ]]; then
        usage >&2
        return "$CI_EXIT_USAGE"
      fi
      base=$2
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
  [[ -n $base ]] || {
    usage >&2
    return "$CI_EXIT_USAGE"
  }
  if [[ $CI_LOG_FORMAT != text && $CI_LOG_FORMAT != json ]] ||
    [[ ! $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]]; then
    usage >&2
    return "$CI_EXIT_USAGE"
  fi

  if [[ $log_enabled == true ]]; then
    ci_log info ci-static start "base=$base" ||
      ci_fail "$CI_EXIT_BLOCKED" 'cannot write static-check log' \
        'Check the log path and jq installation.' ||
      return
  fi
  bless_run "$CI_STATIC_ROOT" changed "$dry_run" "$base" || status=$?
  if [[ $log_enabled == true ]]; then
    ci_log info ci-static finish "status=$status" ||
      ci_fail "$CI_EXIT_BLOCKED" 'cannot write static-check log' \
        'Check the log path and jq installation.' ||
      return
  fi
  return "$status"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
