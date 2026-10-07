#!/usr/bin/env bash
# Shared Bash foundation for repository checks and installed Bash commands.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

[[ ${CI_SKILLS_RUNTIME_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_RUNTIME_LOADED=1

CI_EXIT_USAGE=64
CI_EXIT_DATA=65
CI_EXIT_MISSING=66
CI_EXIT_BLOCKED=69

# Summary: Report a classified failure and the next action.
# Arguments: $1 exit class; $2 message; $3 next action.
# Stderr: bounded diagnostic.
# Returns: supplied exit class.
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

# Summary: Load the single token line from a selected credential file.
# Arguments: file path, output variable name.
# Stdout: none. Stderr: classified failure without credential content.
# Returns: 0, usage, missing input, or invalid credential data.
ci_token_from_file() {
  local path=$1 output_name=$2 value='' extra='' extra_status=0
  [[ $output_name =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return "$CI_EXIT_USAGE"
  [[ -f $path && -r $path ]] || ci_fail "$CI_EXIT_MISSING" \
    'credential file is unreadable' 'Provide a readable --token-file path.' || return $?
  {
    IFS= read -r value || :
    IFS= read -r extra || extra_status=$?
  } <"$path"
  value=${value%$'\r'}
  [[ -n $value ]] || ci_fail "$CI_EXIT_MISSING" \
    'credential file is empty' 'Provide a non-empty --token-file.' || return $?
  if ((extra_status == 0)) || [[ -n $extra ]]; then
    ci_fail "$CI_EXIT_DATA" 'credential file has multiple lines' \
      'Provide one token value in the selected file.'
    return $?
  fi
  [[ ${#value} -le 4096 ]] || ci_fail "$CI_EXIT_DATA" \
    'credential value is too long' 'Check the selected token file.' || return $?
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

# Summary: Identify a clean, tracked skill checkout by its exact Git commit.
# Arguments: absolute source root, tracked manifest path (default: SKILL.md).
# Stdout: full commit SHA. Stderr: classified blocker.
# Returns: zero or blocked class.
ci_verified_source_revision() {
  local source=$1 manifest=${2:-SKILL.md} root='' revision='' changes=''
  root=$(git -C "$source" rev-parse --show-toplevel 2>/dev/null) ||
    ci_fail "$CI_EXIT_BLOCKED" 'skill source is not a Git checkout' \
      'Install from a committed skill checkout.' || return $?
  [[ $root == "$source" ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'skill source is not the checkout root' \
    'Use the repository root that owns SKILL.md.' || return $?
  git -C "$source" ls-files --error-unmatch "$manifest" >/dev/null 2>&1 ||
    ci_fail "$CI_EXIT_BLOCKED" 'skill manifest is not tracked' \
      'Commit the selected skill manifest before installation.' || return $?
  changes=$(git -C "$source" status --porcelain=v1 --untracked-files=all) ||
    ci_fail "$CI_EXIT_BLOCKED" 'skill source status is unreadable' \
      'Inspect the Git checkout before installation.' || return $?
  [[ -z $changes ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'skill source has uncommitted changes' \
    'Commit or remove source changes, then make a new install plan.' || return $?
  revision=$(git -C "$source" rev-parse --verify 'HEAD^{commit}') ||
    ci_fail "$CI_EXIT_BLOCKED" 'skill source revision is unavailable' \
      'Install from a committed skill checkout.' || return $?
  printf '%s\n' "$revision"
}
