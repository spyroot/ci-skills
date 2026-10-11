#!/usr/bin/env bash
# Shared static-check functions for the local pre-commit hook and CI.
# bless.sh is the local staged/worktree entrypoint. scripts/ci/static.sh selects
# the committed comparison range for GitHub Actions.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

[[ ${CI_SKILLS_BLESS_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_BLESS_LOADED=1

# shellcheck source=lib/bash/core/runtime.bash
source "${BASH_SOURCE[0]%/*}/../core/runtime.bash"
# shellcheck source=lib/bash/core/source_graph.bash
source "${BASH_SOURCE[0]%/*}/../core/source_graph.bash"

# The checks, in order. Each name is a function bless_check_<name> called with
# the repository root, the index snapshot and the staged-path list
# (NUL-separated, relative paths). It returns 0 when it passes or has nothing
# to check, 1 when it fails, and CI_EXIT_BLOCKED when a tool it needs is missing.
BLESS_CHECKS='whitespace json schemas yaml markdown bash_syntax shell shfmt actionlint gitleaks python endpoints source_cycle runtime_sync'
BLESS_FAILED=1
# yamllint settings, inline as in the standards repository's bless.sh.
BLESS_YAML_CONFIG='{extends: default, rules: {document-start: disable, line-length: {max: 120}, truthy: disable}}'

# Summary: Print the staged paths that match any of the given shell patterns.
# Arguments: $1 NUL-separated path list; $@ shell patterns after $1.
# Stdout: one NUL-terminated path per match.
# Returns: 0.
bless_select() {
  local list=$1 path pattern
  shift
  while IFS= read -r -d '' path; do
    for pattern in "$@"; do
      # shellcheck disable=SC2053 # the pattern is a glob on purpose
      if [[ $path == $pattern ]]; then
        printf '%s\0' "$path"
        break
      fi
    done
  done <"$list"
}

# Summary: Select shell files by extension or a staged shebang.
# Arguments: $1 snapshot directory; $2 NUL-separated path list.
# Stdout: NUL-terminated shell paths.
# Returns: 0.
bless_select_shell() {
  local snap=$1 list=$2 path first
  while IFS= read -r -d '' path; do
    case $path in
    *.sh | *.bash | *.bats | .githooks/*)
      printf '%s\0' "$path"
      continue
      ;;
    esac
    case ${path##*/} in
    *.*) continue ;;
    esac
    [[ -f $snap/$path ]] || continue
    IFS= read -r first <"$snap/$path" || true
    case $first in
    '#!'*bash* | '#!'*/sh | '#!'*' sh') printf '%s\0' "$path" ;;
    esac
  done <"$list"
}

# Summary: Find a tool: next to the selected Python first, then on PATH.
# Arguments: $1 tool name; $2 Python interpreter, optional.
# Stdout: executable path.
# Stderr: blocker and next step when absent.
# Returns: 0 or CI_EXIT_BLOCKED.
bless_tool() {
  local name=$1 python=${2:-} found
  if [[ -n $python && -x ${python%/*}/$name ]]; then
    printf '%s\n' "${python%/*}/$name"
    return 0
  fi
  if found=$(command -v "$name"); then
    printf '%s\n' "$found"
    return 0
  fi
  ci_fail "$CI_EXIT_BLOCKED" "bless needs $name, which is not installed" \
    "Install $name with the toolchain, then commit again."
}

# Summary: Select a Python 3.11 or newer: the conda environment named in
# environment.yml in the selected Git view, then python3 on PATH.
# Arguments: $1 selected snapshot directory.
# Stdout: interpreter path.
# Stderr: blocker and next step when no compatible interpreter exists.
# Returns: 0 or CI_EXIT_BLOCKED.
bless_python() {
  local snap=$1 name='' conda base candidate
  if [[ -f $snap/environment.yml ]]; then
    name=$(sed -n 's/^name:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$snap/environment.yml" | head -n 1)
  fi
  conda=${CONDA_EXE:-}
  [[ -n $conda ]] || conda=$(command -v conda || true)
  if [[ -n $name && -n $conda ]] && base=$("$conda" info --base 2>/dev/null); then
    candidate=$base/envs/$name/bin/python
    if [[ -x $candidate ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  candidate=$(command -v python3 || true)
  if [[ -n $candidate ]] &&
    "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    printf '%s\n' "$candidate"
    return 0
  fi
  ci_fail "$CI_EXIT_BLOCKED" 'bless needs Python 3.11 or newer' \
    "Create the conda environment from environment.yml (${name:-its name}), then commit again."
}

# Summary: Read NUL-separated paths from stdin into the array BLESS_FILES.
# Stdout: none; sets BLESS_FILES.
# Returns: 0.
bless_read_files() {
  local path
  BLESS_FILES=()
  while IFS= read -r -d '' path; do
    BLESS_FILES+=("$path")
  done
}

# Summary: Write committed ACMR paths after a comparison revision.
# Arguments: $1 repository root; $2 comparison revision; $3 output path.
# Stdout: none.
# Stderr: a blocker when the comparison revision is unavailable.
# Returns: 0 or CI_EXIT_BLOCKED.
bless_changed_paths() {
  local root=$1 base=$2 output=$3
  git -C "$root" cat-file -e "${base}^{commit}" 2>/dev/null ||
    ci_fail "$CI_EXIT_BLOCKED" "static checks cannot resolve base revision $base" \
      'Fetch the comparison base, then run the checks again.' ||
    return
  git -C "$root" diff --name-only -z --diff-filter=ACMR \
    "${base}...HEAD" >"$output" || return "$CI_EXIT_BLOCKED"
}

# Summary: Refuse whitespace errors in the selected Git view.
# Arguments: $1 repository root; $2 snapshot; $3 path inventory; $4 interpreter; $5 scope.
# Stderr: paths containing trailing horizontal whitespace.
# Returns: 0 clean, 1 whitespace found, CI_EXIT_BLOCKED on unreadable files.
bless_check_whitespace() {
  local root=$1 snap=$2 list=$3 scope=${5:-staged} path status failed=0
  if [[ $scope == staged ]]; then
    git -C "$root" diff --cached --check || return "$BLESS_FAILED"
    return 0
  fi
  while IFS= read -r -d '' path; do
    [[ -f $snap/$path ]] || continue
    if LC_ALL=C grep -Iq '[[:blank:]]$' "$snap/$path"; then
      printf 'whitespace: %s has trailing whitespace\n' "$path" >&2
      failed=$BLESS_FAILED
    else
      status=$?
      ((status == 1)) || return "$CI_EXIT_BLOCKED"
    fi
  done <"$list"
  return "$failed"
}

# Summary: Lint selected Markdown from the index snapshot or working tree.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: markdownlint output.
# Stderr: tool diagnostics.
# Returns: 0, 1 for lint failure, or CI_EXIT_BLOCKED for a missing tool.
bless_check_markdown() {
  local snap=$2 list=$3 tool
  bless_read_files < <(bless_select "$list" '*.md' '*.markdown')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool markdownlint-cli2) || return
  (cd "$snap" && "$tool" --config .markdownlint-cli2.yaml "${BLESS_FILES[@]}") ||
    return "$BLESS_FAILED"
}

# Summary: Parse selected Bash and POSIX shell files without executing them.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stderr: Bash parser diagnostics.
# Returns: 0 or 1 for a syntax failure.
bless_check_bash_syntax() {
  local snap=$2 list=$3 path failed=0
  bless_read_files < <(bless_select_shell "$snap" "$list")
  for path in "${BLESS_FILES[@]}"; do
    [[ $path == *.bats ]] && continue
    bash -n "$snap/$path" || failed=$BLESS_FAILED
  done
  return "$failed"
}

# Summary: Lint selected shell files with ShellCheck.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: ShellCheck output.
# Stderr: tool diagnostics.
# Returns: 0, 1 for lint failure, or CI_EXIT_BLOCKED for a missing tool.
bless_check_shell() {
  local snap=$2 list=$3 shellcheck
  bless_read_files < <(bless_select_shell "$snap" "$list")
  ((${#BLESS_FILES[@]})) || return 0
  shellcheck=$(bless_tool shellcheck) || return
  (cd "$snap" && "$shellcheck" -x -S style "${BLESS_FILES[@]}") || return "$BLESS_FAILED"
}

# Summary: Verify selected shell formatting with the repository's two-space style.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: shfmt differences.
# Stderr: tool diagnostics.
# Returns: 0, 1 for formatting differences, or CI_EXIT_BLOCKED when shfmt is absent.
bless_check_shfmt() {
  local snap=$2 list=$3 path shfmt
  local -a files=()
  bless_read_files < <(bless_select_shell "$snap" "$list")
  for path in "${BLESS_FILES[@]}"; do
    [[ $path == *.bats ]] || files+=("$path")
  done
  ((${#files[@]})) || return 0
  shfmt=$(bless_tool shfmt) || return
  (cd "$snap" && "$shfmt" -d -i 2 "${files[@]}") || return "$BLESS_FAILED"
}

# Summary: Validate GitHub workflow syntax when a workflow is selected.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: actionlint diagnostics.
# Stderr: tool diagnostics.
# Returns: 0, 1 for invalid workflow syntax, or CI_EXIT_BLOCKED when actionlint is absent.
bless_check_actionlint() (
  local snap=$2 list=$3 actionlint path
  local -a workflows=()
  bless_read_files < <(bless_select "$list" '.github/workflows/*.yml' '.github/workflows/*.yaml')
  ((${#BLESS_FILES[@]})) || return 0
  shopt -s nullglob
  for path in "$snap"/.github/workflows/*.yml "$snap"/.github/workflows/*.yaml; do
    workflows+=("${path#"$snap"/}")
  done
  ((${#workflows[@]})) || return 0
  actionlint=$(bless_tool actionlint) || return
  (cd "$snap" && "$actionlint" "${workflows[@]}") || return "$BLESS_FAILED"
)

# Summary: Scan selected Git content for committed secrets without printing values.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope; $6 comparison ref.
# Stdout: sanitized gitleaks summary.
# Stderr: tool diagnostics.
# Returns: 0, 1 when a leak is found, or CI_EXIT_BLOCKED when gitleaks is absent.
bless_check_gitleaks() {
  local root=$1 scope=$5 base=${6:-} gitleaks
  gitleaks=$(bless_tool gitleaks) || return
  case $scope in
  staged)
    (cd "$root" && "$gitleaks" git --redact --no-banner --pre-commit --staged .) ||
      return "$BLESS_FAILED"
    ;;
  changed)
    (cd "$root" && "$gitleaks" git --redact --no-banner --log-opts="${base}...HEAD" .) ||
      return "$BLESS_FAILED"
    ;;
  *)
    "$gitleaks" dir --redact --no-banner "$root" || return "$BLESS_FAILED"
    ;;
  esac
}

# Summary: Lint and format-check selected Python with Ruff.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: Ruff output.
# Stderr: tool diagnostics.
# Returns: 0, 1 for lint failure, or CI_EXIT_BLOCKED for a missing tool.
bless_check_python() {
  local snap=$2 list=$3 python=$4 ruff failed=0
  bless_read_files < <(bless_select "$list" '*.py')
  ((${#BLESS_FILES[@]})) || return 0
  ruff=$(bless_tool ruff "$python") || return
  (cd "$snap" && "$ruff" check --no-cache -- "${BLESS_FILES[@]}") || failed=$BLESS_FAILED
  (cd "$snap" && "$ruff" format --check --no-cache -- "${BLESS_FILES[@]}") ||
    failed=$BLESS_FAILED
  return "$failed"
}

# Summary: Lint selected YAML in strict mode.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: yamllint output.
# Stderr: tool diagnostics.
# Returns: 0, 1 for lint failure, or CI_EXIT_BLOCKED for a missing tool.
bless_check_yaml() {
  local snap=$2 list=$3 tool
  bless_read_files < <(bless_select "$list" '*.yml' '*.yaml')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool yamllint) || return
  (cd "$snap" && "$tool" --strict --config-data "$BLESS_YAML_CONFIG" -- "${BLESS_FILES[@]}") ||
    return "$BLESS_FAILED"
}

# Summary: Refuse selected JSON that does not parse as one document.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stderr: failing path and jq diagnostics.
# Returns: 0, 1 for invalid JSON, or CI_EXIT_BLOCKED for a missing tool.
bless_check_json() {
  local snap=$2 list=$3 jq path failed=0
  bless_read_files < <(bless_select "$list" '*.json')
  ((${#BLESS_FILES[@]})) || return 0
  jq=$(bless_tool jq) || return
  for path in "${BLESS_FILES[@]}"; do
    "$jq" -e -s 'length == 1' "$snap/$path" >/dev/null || {
      printf 'json: %s does not parse\n' "$path" >&2
      failed=$BLESS_FAILED
    }
  done
  return "$failed"
}

# Summary: Check selected JSON Schemas against their metaschema.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: checker output.
# Stderr: tool diagnostics.
# Returns: 0, 1 for invalid schema, or CI_EXIT_BLOCKED for a missing tool.
bless_check_schemas() {
  local snap=$2 list=$3 python=$4 tool
  bless_read_files < <(bless_select "$list" 'schemas/*.json')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool check-jsonschema "$python") || return
  (cd "$snap" && "$tool" --check-metaschema "${BLESS_FILES[@]}") || return "$BLESS_FAILED"
}

# Summary: Check target-file keys in the selected Git view.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stdout: endpoint gate result.
# Stderr: endpoint gate diagnostics.
# Returns: 0 or 1 for a gate failure.
bless_check_endpoints() {
  local root=$1 snap=$2 python=$4 scope=$5 gate=$2/gates/gate-ci-skills-endpoints.py
  [[ -f $gate ]] || return 0
  if [[ $scope == staged ]]; then
    "$python" "$gate" --root "$root" --staged || return "$BLESS_FAILED"
  else
    "$python" "$gate" --root "$root" || return "$BLESS_FAILED"
  fi
}

# Summary: Reject a source cycle in Bash files in the selected Git view.
# Arguments: $1 repository root; $2-$4 unused check inputs; $5 scope.
# Stderr: source graph diagnostic.
# Returns: 0 acyclic, 1 cycle or unresolved selected source.
bless_check_source_cycle() {
  local scope=$5
  [[ $scope == changed ]] && scope=all
  (cd "$1" && ci_source_graph_acyclic "$scope") || return "$BLESS_FAILED"
}

# Summary: Verify that the packaged runtime matches the root source.
# Arguments: $1 root; $2 snapshot; $3 path list; $4 interpreter; $5 scope.
# Stderr: mismatch and the two paths to reconcile.
# Returns: 0 on parity or 1 on mismatch.
bless_check_runtime_sync() {
  local root_runtime=$2/lib/bash/core/runtime.bash
  local package_runtime=$2/ci-skills/lib/bash/core/runtime.bash
  if [[ ! -e $root_runtime && ! -e $package_runtime ]]; then
    return 0
  fi
  if ! cmp -s "$root_runtime" "$package_runtime"; then
    printf 'Bash runtime copy differs; copy lib/bash/core/runtime.bash to ci-skills/lib/bash/core/runtime.bash.\n' >&2
    return "$BLESS_FAILED"
  fi
}

# Summary: Run every check on selected bytes, or list them with --dry-run.
# Arguments: $1 repository root; $2 staged, changed, or all; $3 dry run; $4 base ref.
# Stdout: one line per check or a dry-run path list.
# Stderr: failure diagnostics.
# Returns: 0 pass, 1 check failure, CI_EXIT_BLOCKED on unavailable inputs.
bless_run() (
  local root=$1 scope=$2 dry_run=$3 base=${4:-} work status=0
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 ||
    ci_fail "$CI_EXIT_BLOCKED" "$root is not a git checkout" 'Run bless.sh inside the repository.' ||
    return
  work=$(mktemp -d "${TMPDIR:-/tmp}/bless.XXXXXX") ||
    ci_fail "$CI_EXIT_BLOCKED" 'cannot create a working directory' 'Free disk space, then commit again.' ||
    return
  # This subshell owns its snapshot and leaves the caller's EXIT trap intact.
  trap 'rm -rf -- "$work"' EXIT
  bless_run_in "$root" "$work" "$scope" "$dry_run" "$base" || status=$?
  return "$status"
)

# Summary: Build the selected file view and run each registered check.
# Arguments: $1 root; $2 temporary directory; $3 scope; $4 dry run; $5 base ref.
# Stdout: selected paths or check results.
# Stderr: Git, tool, and gate diagnostics.
# Returns: 0 pass, 1 check failure, CI_EXIT_BLOCKED on unavailable inputs.
bless_run_in() {
  local root=$1 work=$2 scope=$3 dry_run=$4 base=${5:-}
  local list=$2/selected snap python check path status failed=0
  if [[ $scope == staged ]]; then
    (cd "$root" && ci_source_graph_selected_staged_paths) >"$list" ||
      return "$CI_EXIT_BLOCKED"
  elif [[ $scope == changed ]]; then
    bless_changed_paths "$root" "$base" "$list" || return
  else
    git -C "$root" ls-files --cached --others --exclude-standard -z >"$work/all" ||
      return "$CI_EXIT_BLOCKED"
    while IFS= read -r -d '' path; do
      [[ -f $root/$path ]] && printf '%s\0' "$path"
    done <"$work/all" >"$list"
  fi
  if [[ ! -s $list ]]; then
    printf 'bless: no files in %s scope\n' "$scope"
    return 0
  fi
  if [[ $scope == staged ]]; then
    snap=$work/index
    git -C "$root" checkout-index --all --prefix="$snap/" || return "$CI_EXIT_BLOCKED"
  else
    snap=$root
  fi
  if [[ $dry_run == true ]]; then
    printf 'bless: dry run; the selected paths are:\n'
    tr '\0' '\n' <"$list"
    printf 'bless: checks: %s\n' "$BLESS_CHECKS"
    return 0
  fi
  python=$(bless_python "$snap") || return
  for check in $BLESS_CHECKS; do
    status=0
    "bless_check_$check" "$root" "$snap" "$list" "$python" "$scope" "$base" || status=$?
    case $status in
    0) printf 'bless: %s ok\n' "$check" ;;
    "$CI_EXIT_BLOCKED") return "$status" ;;
    *)
      printf 'bless: %s FAILED\n' "$check" >&2
      failed=$BLESS_FAILED
      ;;
    esac
  done
  return "$failed"
}
