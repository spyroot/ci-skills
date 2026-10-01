#!/usr/bin/env bash
set -Eeuo pipefail

CI_INSTALL_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib/core/runtime.bash
source "$CI_INSTALL_ROOT/lib/core/runtime.bash"

ci_install_help() {
  cat <<'HELP'
install.sh: link this repository as a Codex skill (audience: agent and human)

Usage:
  ./install.sh [--destination PATH] [--dry-run]
  ./install.sh [--destination PATH] --apply --confirm-install FINGERPRINT

Options:
  --destination PATH     Skill link path; default: $CODEX_HOME/skills/ci-skills
                         or $HOME/.codex/skills/ci-skills.
  --dry-run              Print target and fingerprint; no filesystem change.
  --apply                Create the symlink after fingerprint confirmation.
  --confirm-install SHA  Exact fingerprint printed by dry-run.
  --timeout DURATION     Apply deadline (for example 10s).
  --log-format text|json Diagnostic format (default: text).
  --log-level debug|info|warning|error  Minimum diagnostic level.
  --log-file PATH        Append diagnostics to PATH.
  --run-id ID            Caller-supplied diagnostic correlation ID.
  --help                 Show this help without external dependencies.

Existing files or links to a different source are preserved. Repeating an
installation of the same link is a no-op. Exit: 0 success, 64 usage,
66 missing input, 69 blocked.
HELP
}

ci_install_main() {
  local destination='' mode=dry-run explicit_dry_run=false confirm=''
  local timeout='' seconds='' fingerprint='' started=$SECONDS help=false
  CI_LOG_FORMAT=text CI_LOG_LEVEL=info CI_LOG_FILE='' CI_RUN_ID=''
  if (($# == 0)); then
    ci_install_help >&2
    return "$CI_EXIT_USAGE"
  fi
  while (($#)); do
    case $1 in
    --destination | --confirm-install | --timeout | --log-format | --log-level | --log-file | --run-id)
      (($# >= 2)) || return "$CI_EXIT_USAGE"
      case $1 in
      --destination) destination=$2 ;; --confirm-install) confirm=$2 ;;
      --timeout) timeout=$2 ;; --log-format) CI_LOG_FORMAT=$2 ;;
      --log-level) CI_LOG_LEVEL=$2 ;; --log-file) CI_LOG_FILE=$2 ;;
      --run-id) CI_RUN_ID=$2 ;;
      esac
      shift 2
      ;;
    --dry-run)
      [[ $mode != apply ]] || return "$CI_EXIT_USAGE"
      explicit_dry_run=true
      shift
      ;;
    --apply)
      [[ $explicit_dry_run == false ]] || return "$CI_EXIT_USAGE"
      mode=apply
      shift
      ;;
    --help | -h)
      help=true
      shift
      ;;
    *)
      ci_fail "$CI_EXIT_USAGE" "unknown argument: $1" 'See --help.'
      return $?
      ;;
    esac
  done
  [[ $help == false ]] || {
    ci_install_help
    return 0
  }
  [[ $CI_LOG_FORMAT == text || $CI_LOG_FORMAT == json ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]] || return "$CI_EXIT_USAGE"
  if [[ -z $destination ]]; then
    destination=${CODEX_HOME:-$HOME/.codex}/skills/ci-skills
  fi
  [[ $destination == /* ]] || return "$CI_EXIT_USAGE"
  fingerprint=$(printf '%s\0%s' "$CI_INSTALL_ROOT" "$destination" | ci_sha256_stdin) || return
  if [[ $mode == dry-run ]]; then
    jq -cn --arg source "$CI_INSTALL_ROOT" --arg destination "$destination" \
      --arg fingerprint "$fingerprint" \
      '{mode:"dry-run",source:$source,destination:$destination,fingerprint:$fingerprint}'
    return
  fi
  [[ -n $timeout && -n $confirm ]] || ci_fail "$CI_EXIT_USAGE" \
    'apply requires --timeout and --confirm-install' \
    'Run dry-run, then pass its fingerprint.' || return $?
  [[ $timeout =~ ^[1-9][0-9]*s$ ]] || return "$CI_EXIT_USAGE"
  seconds=${timeout%s}
  [[ $confirm == "$fingerprint" ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'installation fingerprint changed' 'Run dry-run again.' || return $?
  ci_log info ci-install apply "linking ci-skills" || return
  if [[ -L $destination ]]; then
    if [[ $(readlink "$destination") == "$CI_INSTALL_ROOT" ]]; then
      jq -cn --arg destination "$destination" \
        '{status:"NO_OP",destination:$destination}'
      return
    fi
    ci_fail "$CI_EXIT_BLOCKED" 'skill destination points elsewhere' \
      'Choose a different destination or inspect the existing link.'
    return $?
  fi
  [[ ! -e $destination ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'skill destination already exists' 'Inspect it before changing it.' || return $?
  mkdir -p -- "$(dirname -- "$destination")" || return "$CI_EXIT_BLOCKED"
  ((SECONDS - started <= seconds)) || return "$CI_EXIT_BLOCKED"
  ln -s -- "$CI_INSTALL_ROOT" "$destination" || return "$CI_EXIT_BLOCKED"
  [[ -L $destination && $(readlink "$destination") == "$CI_INSTALL_ROOT" ]] ||
    return "$CI_EXIT_BLOCKED"
  jq -cn --arg destination "$destination" \
    '{status:"READY",destination:$destination}'
}

ci_install_main "$@"
