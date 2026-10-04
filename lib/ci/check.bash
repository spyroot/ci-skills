#!/usr/bin/env bash

[[ ${CI_SKILLS_CHECK_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_CHECK_LOADED=1
CI_CHECK_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
# shellcheck source=skills/ci-skills/lib/core/runtime.bash
source "$CI_CHECK_ROOT/skills/ci-skills/lib/core/runtime.bash"

CI_CHECK_GATES=(whitespace bash-n shellcheck shfmt yaml markdown secrets neutrality manifest unit)
declare -gA CI_CHECK_CLASS_BY_GATE=(
  [whitespace]=static
  ["bash-n"]=static
  [shellcheck]=static
  [shfmt]=static
  [yaml]=static
  [markdown]=static
  [secrets]=static
  [neutrality]=static
  [manifest]=static
  [unit]=ci
)
declare -gA CI_CHECK_TOOL_BY_GATE=(
  [shellcheck]=shellcheck
  [shfmt]=shfmt
  [yaml]=yamllint
  [markdown]=markdownlint-cli2
  [secrets]=gitleaks
  [neutrality]=python
  [manifest]=python
  [unit]=bats
)

ci_check_help() {
  cat <<'HELP'
check.sh: validate the tracked ci-skills source in Kubernetes

Usage:
  ./scripts/check.sh [--dry-run] [--gate NAME] [diagnostic options]

Options:
  --dry-run               Print the planned checks without running them.
  --gate NAME             Run one named check, static, or all (default).
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
  printf 'Gate names: %s static\n' "${CI_CHECK_GATES[*]}"
}

# Summary: Check a requested name against the one supported gate registry.
# Arguments: requested gate name.
# Stdout: none. Stderr: none.
# Returns: zero for a supported gate, static, or all; one otherwise.
ci_check_gate_valid() {
  local gate=$1 known
  [[ $gate == all || $gate == static ]] && return 0
  for known in "${CI_CHECK_GATES[@]}"; do
    [[ $gate == "$known" ]] && return 0
  done
  return 1
}

# Summary: Determine whether a requested selection includes a named gate.
# Arguments: selection and gate name.
# Stdout: none. Stderr: none.
# Returns: zero when selected, one otherwise.
ci_check_gate_selected() {
  local selection=$1 gate=$2
  [[ $selection == all || $selection == "$gate" ||
    ($selection == static && ${CI_CHECK_CLASS_BY_GATE[$gate]:-} == static) ]]
}

# Summary: Check whether a required gate executable is available.
# Arguments: executable name.
# Stdout: none. Stderr: none.
# Returns: zero when available, nonzero otherwise.
ci_check_tool_present() {
  command -v "$1" >/dev/null 2>&1
}

# Summary: Confirm every required check command is available in the pod.
# Arguments: selected gate name, or all by default.
# Stdout: none. Stderr: one classified missing-tool diagnostic.
# Returns: zero or blocked class.
ci_check_tools() {
  local gate=${1:-all} selected tool
  local -a tools=(bash git jq)
  if [[ $gate == all || $gate == static ]]; then
    for selected in "${CI_CHECK_GATES[@]}"; do
      ci_check_gate_selected "$gate" "$selected" || continue
      tool=${CI_CHECK_TOOL_BY_GATE[$selected]:-}
      [[ -z $tool ]] || tools+=("$tool")
    done
    [[ $gate != all ]] || tools+=(yq gh glab)
  else
    tool=${CI_CHECK_TOOL_BY_GATE[$gate]:-}
    [[ -z $tool ]] || tools+=("$tool")
  fi
  for tool in "${tools[@]}"; do
    if ! ci_check_tool_present "$tool"; then
      ci_fail "$CI_EXIT_BLOCKED" "required check tool is missing: $tool" \
        'Use the approved CI image containing all declared tools.'
      return $?
    fi
  done
}

# Summary: Render the selected check names for plans and final results.
# Arguments: selected gate name, or all.
# Stdout: JSON array. Stderr: jq diagnostics.
# Returns: zero or jq's failure status.
ci_check_gate_list_json() {
  local selection=$1 gate
  local -a selected=()
  for gate in "${CI_CHECK_GATES[@]}"; do
    ci_check_gate_selected "$selection" "$gate" && selected+=("$gate")
  done
  printf '%s\n' "${selected[@]}" | jq -Rsc 'split("\n")[:-1]'
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
# Arguments: selected gate name, or all by default.
# Stdout: one JSON success result. Stderr: check output and diagnostics.
# Returns: zero or blocked class.
ci_check_run() {
  local gate=${1:-all} file empty_tree verified_revision final_revision checks_json
  local -a shell_files=() yaml_files=() markdown_files=()
  cd "$CI_CHECK_ROOT" || return "$CI_EXIT_BLOCKED"
  if [[ $gate != static ]]; then
    verified_revision=$(ci_verified_source_revision "$CI_CHECK_ROOT" \
      'skills/ci-skills/SKILL.md') || return $?
  fi
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
  if ci_check_gate_selected "$gate" whitespace; then
    ci_check_step whitespace git diff --check "$empty_tree" HEAD || return $?
    if [[ $gate == static ]]; then
      ci_check_step whitespace-staged git diff --cached --check || return $?
      ci_check_step whitespace-unstaged git diff --check || return $?
    fi
  fi
  if ci_check_gate_selected "$gate" bash-n; then
    for file in "${shell_files[@]}"; do
      ci_check_step "bash-n:$file" bash -n "$file" || return $?
    done
  fi
  if ci_check_gate_selected "$gate" shellcheck; then
    ci_check_step shellcheck shellcheck -x -S style "${shell_files[@]}" || return $?
  fi
  if ci_check_gate_selected "$gate" shfmt; then
    ci_check_step shfmt shfmt -i 2 -d "${shell_files[@]}" || return $?
  fi
  if ci_check_gate_selected "$gate" yaml; then
    ci_check_step yaml yamllint \
      --config-data '{extends: default, rules: {document-start: disable, line-length: disable, truthy: disable}}' \
      "${yaml_files[@]}" || return $?
  fi
  if ci_check_gate_selected "$gate" markdown; then
    ci_check_step markdown markdownlint-cli2 "${markdown_files[@]}" || return $?
  fi
  if ci_check_gate_selected "$gate" secrets; then
    ci_check_step secrets gitleaks git --redact --no-banner . || return $?
  fi
  if ci_check_gate_selected "$gate" neutrality; then
    ci_check_step neutrality python tools/check_project_neutrality.py \
      --root . --json || return $?
  fi
  if ci_check_gate_selected "$gate" manifest; then
    ci_check_step manifest python tools/render_manifest.py --check || return $?
  fi
  if ci_check_gate_selected "$gate" unit; then
    ci_check_step unit bats --tap tests || return $?
  fi
  checks_json=$(ci_check_gate_list_json "$gate") || return "$CI_EXIT_BLOCKED"
  if [[ $gate == static ]]; then
    jq -cn --argjson checks "$checks_json" \
      '{status:"passed",source:"working-tree",commit:null,checks:$checks}'
    return
  fi
  final_revision=$(ci_verified_source_revision "$CI_CHECK_ROOT" \
    'skills/ci-skills/SKILL.md') || return $?
  [[ $final_revision == "$verified_revision" ]] || ci_fail "$CI_EXIT_BLOCKED" \
    'source revision changed during validation' \
    'Rerun this gate against one stable exact source commit.' || return $?
  jq -cn --arg commit "$verified_revision" --argjson checks "$checks_json" \
    '{status:"passed",commit:$commit,checks:$checks}'
}

# Summary: Parse gate options and run validation only inside Kubernetes.
# Arguments: command-line arguments.
# Stdout: JSON plan or result. Stderr: classified diagnostics.
# Returns: zero, usage, or blocked class.
ci_check_main() {
  local dry_run=false help=false gate=all checks_json
  CI_LOG_FORMAT=text CI_LOG_LEVEL=info CI_LOG_FILE='' CI_RUN_ID=''
  while (($#)); do
    case $1 in
    --dry-run)
      dry_run=true
      shift
      ;;
    --gate)
      (($# >= 2)) || return "$CI_EXIT_USAGE"
      gate=$2
      shift 2
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
  if ! ci_check_gate_valid "$gate"; then
    ci_fail "$CI_EXIT_USAGE" "unknown gate: $gate" 'See --help for gate names.'
    return $?
  fi
  if [[ -n $CI_LOG_FILE ]]; then
    CI_LOG_FILE=$(ci_check_log_file_path "$CI_LOG_FILE") || return $?
  fi
  if [[ $dry_run == true ]]; then
    command -v jq >/dev/null 2>&1 || return "$CI_EXIT_BLOCKED"
    checks_json=$(ci_check_gate_list_json "$gate") || return "$CI_EXIT_BLOCKED"
    jq -cn --argjson checks "$checks_json" \
      '{mode:"dry-run",location:"Kubernetes pod",checks:$checks}'
    return
  fi
  ((BASH_VERSINFO[0] >= 5)) || ci_fail "$CI_EXIT_BLOCKED" \
    'Bash 5 is required' 'Use a CI pod image with Bash 5.' || return $?
  if [[ $gate != static ]]; then
    [[ -n ${KUBERNETES_SERVICE_HOST:-} ]] || ci_fail "$CI_EXIT_BLOCKED" \
      'Kubernetes execution is required' \
      'Run this gate in the project-approved Kubernetes CI job.' || return $?
  fi
  ci_check_tools "$gate" || return $?
  ci_check_run "$gate"
}
