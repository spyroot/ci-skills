#!/usr/bin/env bash

[[ ${CI_SKILLS_RUNTIME_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_RUNTIME_LOADED=1

CI_EXIT_USAGE=64
CI_EXIT_DATA=65
CI_EXIT_MISSING=66
CI_EXIT_BLOCKED=69

# Summary: Report a classified failure and the next action.
# Arguments: exit class, message, next action.
# Stderr: bounded diagnostic. Returns: supplied exit class.
ci_fail() {
  local status=$1 message=$2 next=$3
  printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$message" "$next" >&2
  return "$status"
}

# Summary: Write one diagnostic at or above the requested log level.
# Arguments: level, component, event, message.
# Stderr: text or JSON Lines; optional log file receives the same line.
# Returns: 0 if written or suppressed; failure if the log cannot be written.
ci_log() {
  local level=$1 component=$2 event=$3 message=$4 line timestamp
  local rank=0 minimum=0
  case $level in
    debug) rank=0 ;; info) rank=1 ;; warning) rank=2 ;; error) rank=3 ;;
    *) return "$CI_EXIT_USAGE" ;;
  esac
  case ${CI_LOG_LEVEL:-info} in
    debug) minimum=0 ;; info) minimum=1 ;; warning) minimum=2 ;;
    error) minimum=3 ;; *) return "$CI_EXIT_USAGE" ;;
  esac
  ((rank >= minimum)) || return 0
  timestamp=$(date -u '+%Y-%m-%dT%H:%M:%SZ') || return
  if [[ ${CI_LOG_FORMAT:-text} == json ]]; then
    line=$(jq -cn --arg ts "$timestamp" --arg level "$level" \
      --arg run "${CI_RUN_ID:-}" --arg component "$component" \
      --arg event "$event" --arg message "$message" \
      '{timestamp:$ts,level:$level,runId:$run,component:$component,event:$event,message:$message}') || return
  else
    line="$timestamp $level $component $event: $message"
  fi
  printf '%s\n' "$line" >&2
  if [[ -n ${CI_LOG_FILE:-} ]]; then
    printf '%s\n' "$line" >>"$CI_LOG_FILE" || return
  fi
}

# Summary: Load the complete first line of a selected credential file.
# Arguments: file path, output variable name.
# Stdout: none. Stderr: classified failure without credential content.
# Returns: 0, usage, missing file, or blocked empty credential.
ci_token_from_file() {
  local path=$1 output_name=$2 value=''
  [[ $output_name =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return "$CI_EXIT_USAGE"
  [[ -r $path ]] || ci_fail "$CI_EXIT_MISSING" \
    'credential file is unreadable' 'Provide a readable --token-file path.' || return $?
  if IFS= read -r value <"$path"; then
    :
  elif [[ -z $value ]]; then
    ci_fail "$CI_EXIT_MISSING" 'credential file is empty' \
      'Provide a non-empty --token-file.'
    return $?
  fi
  value=${value%$'\r'}
  [[ -n $value ]] || ci_fail "$CI_EXIT_MISSING" \
    'credential file is empty' 'Provide a non-empty --token-file.' || return $?
  printf -v "$output_name" '%s' "$value"
}

# Summary: Calculate the SHA-256 of bytes read from stdin.
# Stdout: lowercase hex digest. Returns: hash command status.
ci_sha256_stdin() {
  local result=''
  if command -v shasum >/dev/null 2>&1; then
    result=$(shasum -a 256) || return
  elif command -v sha256sum >/dev/null 2>&1; then
    result=$(sha256sum) || return
  else
    ci_fail "$CI_EXIT_BLOCKED" 'SHA-256 command is unavailable' \
      'Install shasum or sha256sum.'
    return $?
  fi
  printf '%s\n' "${result%% *}"
}
