#!/usr/bin/env bash

[[ ${CI_SKILLS_API_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_API_LOADED=1
CI_API_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
# shellcheck source=lib/core/runtime.bash
source "$CI_API_ROOT/lib/core/runtime.bash"

ci_api_help() {
  cat <<'HELP'
ci-api: bounded authenticated GitHub or GitLab reads (audience: agent and human)

Usage:
  ci-api check --provider github|gitlab [--host HOST] [--token-file PATH]
  ci-api get --provider github|gitlab --endpoint API_PATH [options]

Options:
  --host HOST             Required for GitLab; GitHub defaults to github.com.
  --token-file PATH       Optional credential file; otherwise use gh/glab auth.
  --field DOT.PATH        Select one JSON field, such as content.
  --decode-base64         Decode the selected base64 string.
  --lines START:END       Return only a bounded source line range (at most 200).
  --paginate             Fetch all result pages; use only for collections.
  --output json|text      Result format (default: json).
  --dry-run               Print request plan; perform no API call.
  --log-format text|json  Diagnostic format (default: text).
  --log-level debug|info|warning|error  Minimum diagnostic level.
  --log-file PATH         Append diagnostics to PATH; never write credentials.
  --run-id ID             Caller-supplied diagnostic correlation ID.
  --help                  Show this help without external dependencies.

Only GET requests are supported. Missing fields and invalid encodings fail.
Results are bounded to 1 MiB; use --field and --lines for large files.
Retryable GET failures (HTTP 408, 429, 5xx, or transport errors) receive at
most two retries. A Retry-After delay above five seconds stops the request.
Exit: 0 success, 64 usage, 65 invalid API data, 66 missing input, 69 blocked.

Example:
  ci-api get --provider github --endpoint \
    'repos/actions/runner/contents/src/Runner.Common/HostContext.cs?ref=v2.337.0' \
    --field content --decode-base64 --lines 445:535 --output text
HELP
}

# Summary: Validate the caller-selected field path and extract it from JSON.
# Arguments: JSON response and dotted field path.
# Stdout: selected JSON value. Stderr: jq diagnostics.
# Returns: 0 or invalid-data class.
ci_api_select_field() {
  local response=$1 field=$2
  [[ $field =~ ^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$ ]] ||
    return "$CI_EXIT_USAGE"
  jq -ce --arg field "$field" \
    'getpath($field|split(".")) | select(. != null)' <<<"$response"
}

# Summary: Recognize provider failures safe to retry for a GET.
# Arguments: provider diagnostic text.
# Returns: 0 for a retryable status or transport failure; 1 otherwise.
ci_api_retryable_error() {
  local diagnostic=$1
  [[ $diagnostic =~ HTTP[[:space:]]+(408|429|5[0-9][0-9]) ]] ||
    [[ $diagnostic == *'timed out'* ||
       $diagnostic == *'connection reset'* ||
       $diagnostic == *'Temporary failure'* ]]
}

# Summary: Wait within the fixed retry budget without displaying diagnostics.
# Arguments: attempt number, provider diagnostic text.
# Returns: 0 after the delay; blocked class if Retry-After exceeds the budget.
ci_api_retry_delay() {
  local attempt=$1 diagnostic=$2 delay=''
  if [[ $diagnostic =~ [Rr]etry-[Aa]fter:[[:space:]]*([0-9]{1,5}) ]]; then
    delay=${BASH_REMATCH[1]}
    ((10#$delay <= 5)) || ci_fail "$CI_EXIT_BLOCKED" \
      'provider Retry-After exceeds the five-second retry budget' \
      'Retry the same read after the provider delay.' || return $?
  else
    delay=$((attempt + RANDOM % 2))
  fi
  sleep "$delay" || return "$CI_EXIT_BLOCKED"
}

# Summary: Call one provider API with explicit GET and optional credential file.
# Arguments: provider, host, endpoint, paginate boolean, token file.
# Stdout: provider JSON response. Stderr: classified failure.
# Returns: 0 or provider failure status.
ci_api_get() {
  local provider=$1 host=$2 endpoint=$3 paginate=$4 token_file=$5
  local token='' response='' status=0 error_class=PROVIDER_FAILED attempt=0
  local -a request=()
  if [[ -n $token_file ]]; then
    ci_token_from_file "$token_file" token || return $?
  fi
  if [[ $provider == github ]]; then
    request=(gh api --method GET --hostname "$host" "$endpoint")
    if [[ $paginate == true ]]; then request+=(--paginate --slurp); fi
  else
    request=(glab api --method GET --hostname "$host" "$endpoint")
    if [[ $paginate == true ]]; then request+=(--paginate); fi
  fi
  for ((attempt=1; attempt<=3; attempt++)); do
    status=0
    if [[ -n $token && $provider == github ]]; then
      response=$(GH_TOKEN="$token" "${request[@]}" 2>&1) || status=$?
    elif [[ -n $token ]]; then
      response=$(GITLAB_TOKEN="$token" "${request[@]}" 2>&1) || status=$?
    else
      response=$("${request[@]}" 2>&1) || status=$?
    fi
    ((status != 0)) || break
    if [[ $response == *401* || $response == *403* ||
          $response == *Unauthorized* || $response == *Forbidden* ]]; then
      error_class=AUTHORIZATION_FAILED
      break
    fi
    ci_api_retryable_error "$response" || break
    ((attempt < 3)) || break
    ci_log warning ci-api retry "retryable provider failure, attempt $attempt" || return
    ci_api_retry_delay "$attempt" "$response" || return $?
  done
  if ((status != 0)); then
    ci_fail "$CI_EXIT_BLOCKED" "$provider API $error_class (exit $status)" \
      'Check the selected host, endpoint, and provider authentication.'
    return $?
  fi
  [[ ${#response} -le 1048576 ]] || ci_fail "$CI_EXIT_DATA" \
    'API response exceeds 1 MiB' 'Select a field or a narrower endpoint.' || return $?
  jq -e . >/dev/null <<<"$response" || ci_fail "$CI_EXIT_DATA" \
    'provider returned invalid JSON' 'Check the endpoint and provider.' || return $?
  printf '%s\n' "$response"
}

# Summary: Handle one read-only API operation and its output transforms.
# Arguments: command-line arguments.
# Stdout: JSON plan/result or selected text. Stderr: classified diagnostics.
# Returns: 0, usage, data, missing, or blocked class.
ci_api_main() {
  local operation='' provider='' host='' endpoint='' token_file='' field=''
  local output=json paginate=false dry_run=false decode=false lines=''
  local help=false response='' selected='' start='' end='' text_value=''
  CI_LOG_FORMAT=text CI_LOG_LEVEL=info CI_LOG_FILE='' CI_RUN_ID=''
  if (($# == 0)); then ci_api_help >&2; return "$CI_EXIT_USAGE"; fi
  case $1 in
    check|get) operation=$1; shift ;;
    --help|-h) ci_api_help; return 0 ;;
    *) ci_api_help >&2; return "$CI_EXIT_USAGE" ;;
  esac
  while (($#)); do
    case $1 in
      --provider|--host|--endpoint|--token-file|--field|--lines|--output|--log-format|--log-level|--log-file|--run-id)
        (($# >= 2)) || ci_fail "$CI_EXIT_USAGE" "$1 needs a value" 'See --help.' || return $?
        case $1 in
          --provider) provider=$2 ;; --host) host=$2 ;; --endpoint) endpoint=$2 ;;
          --token-file) token_file=$2 ;; --field) field=$2 ;;
          --lines) lines=$2 ;; --output) output=$2 ;;
          --log-format) CI_LOG_FORMAT=$2 ;; --log-level) CI_LOG_LEVEL=$2 ;;
          --log-file) CI_LOG_FILE=$2 ;; --run-id) CI_RUN_ID=$2 ;;
        esac
        shift 2 ;;
      --decode-base64) decode=true; shift ;;
      --paginate) paginate=true; shift ;;
      --dry-run) dry_run=true; shift ;;
      --help|-h) help=true; shift ;;
      *) ci_fail "$CI_EXIT_USAGE" "unknown argument: $1" 'See --help.'; return $? ;;
    esac
  done
  [[ $help == false ]] || { ci_api_help; return 0; }
  [[ $provider == github || $provider == gitlab ]] ||
    ci_fail "$CI_EXIT_USAGE" 'select --provider github or gitlab' 'See --help.' || return $?
  [[ $output == json || $output == text ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_FORMAT == text || $CI_LOG_FORMAT == json ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]] || return "$CI_EXIT_USAGE"
  if [[ -z $host && $provider == github ]]; then host=github.com; fi
  [[ -n $host ]] || ci_fail "$CI_EXIT_USAGE" 'GitLab requires --host' \
    'Pass the selected GitLab host.' || return $?
  [[ $host != *'/'* && $host != *'@'* && $host != *':'* ]] || return "$CI_EXIT_USAGE"
  if [[ $operation == check ]]; then
    [[ -z $endpoint && -z $field && -z $lines && $decode == false && $paginate == false ]] || return "$CI_EXIT_USAGE"
    endpoint=user
  else
    [[ -n $endpoint && $endpoint != /* && $endpoint != *://* ]] || return "$CI_EXIT_USAGE"
  fi
  if [[ -n $lines ]]; then
    [[ $lines =~ ^[1-9][0-9]*:[1-9][0-9]*$ ]] || return "$CI_EXIT_USAGE"
    start=${lines%%:*}; end=${lines##*:}
    ((end >= start && end - start < 200)) || return "$CI_EXIT_USAGE"
    [[ $decode == true ]] || return "$CI_EXIT_USAGE"
  fi
  [[ $decode == false || -n $field ]] || return "$CI_EXIT_USAGE"
  if [[ $output == text && -z $field && $operation != check ]]; then return "$CI_EXIT_USAGE"; fi
  if [[ $dry_run == true ]]; then
    jq -cn --arg operation "$operation" --arg provider "$provider" --arg host "$host" \
      --arg endpoint "$endpoint" --arg field "$field" --arg lines "$lines" \
      --argjson decode "$decode" --argjson paginate "$paginate" \
      '{mode:"dry-run",operation:$operation,provider:$provider,host:$host,endpoint:$endpoint,field:$field,decodeBase64:$decode,lines:$lines,paginate:$paginate}'
    return
  fi
  command -v jq >/dev/null 2>&1 || ci_fail "$CI_EXIT_BLOCKED" 'jq is missing' 'Install jq.' || return $?
  local provider_bin=gh
  [[ $provider == github ]] || provider_bin=glab
  command -v "$provider_bin" >/dev/null 2>&1 || ci_fail "$CI_EXIT_BLOCKED" \
    "$provider_bin is missing" "Install $provider_bin." || return $?
  ci_log info ci-api request "$provider $operation on $host" || return
  response=$(ci_api_get "$provider" "$host" "$endpoint" "$paginate" "$token_file") || return $?
  if [[ $operation == check ]]; then
    if [[ $output == text ]]; then
      jq -r '.login // .username // .name // empty' <<<"$response"
    else
      jq -c --arg provider "$provider" --arg host "$host" \
        '{provider:$provider,host:$host,authenticated:true,user:(.login // .username // .name // null),id:(.id // null)}' <<<"$response"
    fi
    return
  fi
  selected=$response
  if [[ -n $field ]]; then
    selected=$(ci_api_select_field "$response" "$field") || ci_fail "$CI_EXIT_DATA" \
      'selected field is missing or invalid' 'Check --field against the response shape.' || return $?
  fi
  if [[ $decode == true ]]; then
    text_value=$(jq -er '@base64d' <<<"$selected") || ci_fail "$CI_EXIT_DATA" \
      'selected field is not valid base64 text' 'Select an encoded content field.' || return $?
    [[ ${#text_value} -le 1048576 ]] || return "$CI_EXIT_DATA"
    if [[ -n $lines ]]; then
      text_value=$(awk -v first="$start" -v last="$end" \
        'NR >= first && NR <= last {printf "%d:%s\n", NR, $0}' <<<"$text_value")
    fi
    if [[ $output == text ]]; then
      printf '%s\n' "$text_value"
    else
      jq -cn --arg provider "$provider" --arg host "$host" \
        --arg endpoint "$endpoint" --arg field "$field" \
        --arg lines "$lines" --arg content "$text_value" \
        '{provider:$provider,host:$host,endpoint:$endpoint,field:$field,lines:$lines,content:$content}'
    fi
  elif [[ $output == text ]]; then
    jq -er 'if type == "string" or type == "number" or type == "boolean" then tostring else error("field is not scalar") end' <<<"$selected"
  else
    printf '%s\n' "$selected" | jq -c .
  fi
}
