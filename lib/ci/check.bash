#!/usr/bin/env bash

[[ ${CI_SKILLS_CHECK_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_CHECK_LOADED=1
CI_CHECK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
# shellcheck source=lib/core/runtime.bash
source "$CI_CHECK_ROOT/lib/core/runtime.bash"

ci_check_help() {
  cat <<'HELP'
check.sh: validate the tracked ci-skills source in Kubernetes

Usage:
  ./scripts/check.sh [--dry-run] [diagnostic options]

Options:
  --dry-run               Print the planned checks without running them.
  --log-format text|json  Diagnostic format (default: text).
  --log-level debug|info|warning|error  Minimum diagnostic level.
  --log-file PATH         Append diagnostics to PATH when requested.
  --run-id ID             Caller-supplied diagnostic correlation ID.
  --help                  Show this help without external dependencies.

The live gate requires a Kubernetes pod. It checks all tracked shell source
with bash -n, ShellCheck, and shfmt; checks tracked whitespace, YAML and
Markdown; scans Git history for secrets; and runs the Bats suite. Exit 0
means every check passed. Exit 64 means invalid usage; 69 means blocked or
failed validation. Dry-run performs no checks and is allowed off cluster.
HELP
}

# Summary: Confirm every required check command is available in the pod.
# Arguments: none.
# Stdout: none. Stderr: one classified missing-tool diagnostic.
# Returns: zero or blocked class.
ci_check_tools() {
  local tool
  for tool in bash bats git gitleaks jq markdownlint-cli2 shellcheck shfmt \
    yamllint yq gh glab; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      ci_fail "$CI_EXIT_BLOCKED" "required check tool is missing: $tool" \
        'Use the approved CI image containing all declared tools.'
      return $?
    fi
  done
}

# Summary: Run one named validation and preserve its failure as a gate failure.
# Arguments: check name and command argument vector.
# Stdout: none. Stderr: child output, diagnostics, and classified failure.
# Returns: zero or blocked class.
ci_check_step() {
  local name=$1 status=0
  shift
  ci_log info ci-check "$name" started || return
  "$@" >&2 || status=$?
  if ((status != 0)); then
    ci_fail "$CI_EXIT_BLOCKED" "$name failed (exit $status)" \
      'Fix the reported source or test failure and rerun this gate.'
    return $?
  fi
}

# Summary: Reject diagnostic log files that would dirty the source checkout.
# Arguments: requested log file path.
# Stdout: canonical log path. Stderr: classified blocker for unsafe paths.
# Returns: zero, missing, or blocked class.
ci_check_log_file_path() {
  local path=$1 parent='' base='' parent_real='' root_real='' target=''
  [[ -n $path ]] || return "$CI_EXIT_USAGE"
  parent=$(dirname -- "$path")
  base=$(basename -- "$path")
  parent_real=$(cd "$parent" 2>/dev/null && pwd -P) ||
    ci_fail "$CI_EXIT_MISSING" 'log file parent directory is unreadable' \
      'Create the selected log directory outside this checkout, then rerun this gate.' ||
    return $?
  root_real=$(cd "$CI_CHECK_ROOT" 2>/dev/null && pwd -P) ||
    ci_fail "$CI_EXIT_BLOCKED" 'source tree path is unreadable' \
      'Run this gate from the checked-out repository root.' || return $?
  target="${parent_real}/${base}"
  if [[ -L $target ]]; then
    ci_fail "$CI_EXIT_BLOCKED" 'log file must not be a symlink' \
      'Choose a regular --log-file path outside this repository.' || return $?
  fi
  case "$target" in
  "$root_real" | "$root_real"/*)
    ci_fail "$CI_EXIT_BLOCKED" 'log file must be outside the source checkout' \
      'Choose a --log-file path outside this repository.' || return $?
    ;;
  esac
  printf '%s\n' "$target"
}

# Summary: Run the tracked shell, metadata, secret, and Bats checks in a pod.
# Arguments: none.
# Stdout: one JSON success result. Stderr: check output and diagnostics.
# Returns: zero or blocked class.
ci_check_run() {
  local file empty_tree verified_revision final_revision
  local -a shell_files=() yaml_files=() markdown_files=()
  cd "$CI_CHECK_ROOT" || return "$CI_EXIT_BLOCKED"
  verified_revision=$(ci_verified_source_revision "$CI_CHECK_ROOT") || return $?
  while IFS= read -r -d '' file; do shell_files+=("$file"); done \
    < <(git ls-files -z -- '*.sh' '*.bash' 'bin/*')
  while IFS= read -r -d '' file; do yaml_files+=("$file"); done \
    < <(git ls-files -z -- '*.yaml' '*.yml')
  while IFS= read -r -d '' file; do markdown_files+=("$file"); done \
    < <(git ls-files -z -- '*.md')
  ((${#shell_files[@]} > 0 && ${#yaml_files[@]} > 0 && \
  ${#markdown_files[@]} > 0)) || ci_fail "$CI_EXIT_BLOCKED" \
    'tracked validation inputs are missing' \
    'Check the CI checkout and tracked file inventory.' || return $?

  empty_tree=$(git hash-object -t tree /dev/null) || return "$CI_EXIT_BLOCKED"
  ci_check_step whitespace git diff --check "$empty_tree" HEAD || return $?
  for file in "${shell_files[@]}"; do
    ci_check_step "bash-n:$file" bash -n "$file" || return $?
  done
  ci_check_step shellcheck shellcheck -x -S style "${shell_files[@]}" || return $?
  ci_check_step shfmt shfmt -i 2 -d "${shell_files[@]}" || return $?
  ci_check_step yaml yamllint \
    --config-data '{extends: default, rules: {document-start: disable, line-length: disable, truthy: disable}}' \
    "${yaml_files[@]}" || return $?
  ci_check_step markdown markdownlint-cli2 "${markdown_files[@]}" || return $?
  ci_check_step secrets gitleaks git --redact --no-banner . || return $?
  ci_check_step unit bats --tap tests || return $?
  final_revision=$(ci_verified_source_revision "$CI_CHECK_ROOT") || return $?
  [[ $final_revision == "$verified_revision" ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'source revision changed during validation' \
    'Rerun this gate against one stable exact source commit.' || return $?
  jq -cn --arg commit "$verified_revision" \
    '{status:"passed",commit:$commit,checks:["whitespace","bash-n","shellcheck","shfmt","yaml","markdown","secrets","unit"]}'
}

# Summary: Parse gate options and run validation only inside Kubernetes.
# Arguments: command-line arguments.
# Stdout: JSON plan or result. Stderr: classified diagnostics.
# Returns: zero, usage, or blocked class.
ci_check_main() {
  local dry_run=false help=false
  CI_LOG_FORMAT=text CI_LOG_LEVEL=info CI_LOG_FILE='' CI_RUN_ID=''
  while (($#)); do
    case $1 in
    --dry-run)
      dry_run=true
      shift
      ;;
    --log-format | --log-level | --log-file | --run-id)
      (($# >= 2)) || return "$CI_EXIT_USAGE"
      case $1 in
      --log-format) CI_LOG_FORMAT=$2 ;; --log-level) CI_LOG_LEVEL=$2 ;;
      --log-file) CI_LOG_FILE=$2 ;; --run-id) CI_RUN_ID=$2 ;;
      esac
      shift 2
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
    ci_check_help
    return 0
  }
  [[ $CI_LOG_FORMAT == text || $CI_LOG_FORMAT == json ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]] || return "$CI_EXIT_USAGE"
  [[ $CI_RUN_ID =~ ^[A-Za-z0-9._:-]*$ ]] || return "$CI_EXIT_USAGE"
  if [[ -n $CI_LOG_FILE ]]; then
    CI_LOG_FILE=$(ci_check_log_file_path "$CI_LOG_FILE") || return $?
  fi
  if [[ $dry_run == true ]]; then
    command -v jq >/dev/null 2>&1 || return "$CI_EXIT_BLOCKED"
    jq -cn '{mode:"dry-run",location:"Kubernetes pod",checks:["whitespace","bash-n","shellcheck","shfmt","yaml","markdown","secrets","unit"]}'
    return
  fi
  ((BASH_VERSINFO[0] >= 5)) || ci_fail "$CI_EXIT_BLOCKED" \
    'Bash 5 is required' 'Use a CI pod image with Bash 5.' || return $?
  [[ -n ${KUBERNETES_SERVICE_HOST:-} ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'Kubernetes execution is required' \
    'Run this gate in the project-approved Kubernetes CI job.' || return $?
  ci_check_tools || return $?
  ci_check_run
}
