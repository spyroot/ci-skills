#!/usr/bin/env bash
# The pre-commit checks bless.sh runs on the staged bytes. bless.sh (repository
# root) is the thin entry point; .githooks/pre-commit calls it, and `make bless`
# activates that hook (core.hooksPath .githooks) and runs it.
# Written for Bash 3.2 as well, because git may run the hook with /bin/bash.

[[ ${CI_SKILLS_BLESS_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_BLESS_LOADED=1

# The checks, in order. Each name is a function bless_check_<name> called with
# the repository root, the index snapshot and the staged-path list
# (NUL-separated, relative paths). It returns 0 when it passes or has nothing
# to check, 1 when it fails, and CI_EXIT_BLOCKED when a tool it needs is missing.
BLESS_CHECKS='whitespace json schemas yaml markdown shell python endpoints'
BLESS_FAILED=1
# yamllint settings, inline as in the standards repository's bless.sh.
BLESS_YAML_CONFIG='{extends: default, rules: {document-start: disable, line-length: {max: 120}, truthy: disable}}'

# Summary: Print the staged paths that match any of the given shell patterns.
# Arguments: staged-path list, pattern...
# Stdout: one NUL-terminated path per match. Returns: 0.
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

# Summary: Print the staged shell files: by extension, or by a sh/bash shebang
# in the staged bytes of an extensionless file.
# Arguments: snapshot directory, staged-path list.
# Stdout: NUL-terminated paths. Returns: 0.
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
# Arguments: tool name, Python interpreter (may be empty).
# Stdout: the executable path. Returns: 0, or CI_EXIT_BLOCKED when absent.
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
# environment.yml when conda is available, otherwise python3 on PATH.
# Arguments: repository root. Stdout: the interpreter. Returns: 0 or blocked.
bless_python() {
  local root=$1 name='' conda base candidate
  if [[ -f $root/environment.yml ]]; then
    name=$(sed -n 's/^name:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$root/environment.yml" | head -n 1)
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
# Returns: 0. Bash 3.2 has no mapfile -d.
bless_read_files() {
  local path
  BLESS_FILES=()
  while IFS= read -r -d '' path; do
    BLESS_FILES+=("$path")
  done
}

# Summary: Refuse whitespace errors and conflict markers in the staged diff.
bless_check_whitespace() {
  git -C "$1" diff --cached --check || return "$BLESS_FAILED"
}

# Summary: Lint the staged Markdown with the repository's markdownlint config.
bless_check_markdown() {
  local snap=$2 list=$3 tool
  bless_read_files < <(bless_select "$list" '*.md' '*.markdown')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool markdownlint-cli2) || return
  (cd "$snap" && "$tool" --config .markdownlint-cli2.yaml "${BLESS_FILES[@]}") ||
    return "$BLESS_FAILED"
}

# Summary: Lint the staged shell files with shellcheck.
bless_check_shell() {
  local snap=$2 list=$3 shellcheck
  bless_read_files < <(bless_select_shell "$snap" "$list")
  ((${#BLESS_FILES[@]})) || return 0
  shellcheck=$(bless_tool shellcheck) || return
  (cd "$snap" && "$shellcheck" -x "${BLESS_FILES[@]}") || return "$BLESS_FAILED"
}

# Summary: Lint and format-check the staged Python with the repository's ruff
# configuration.
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

# Summary: Lint the staged YAML in strict mode.
bless_check_yaml() {
  local snap=$2 list=$3 tool
  bless_read_files < <(bless_select "$list" '*.yml' '*.yaml')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool yamllint) || return
  (cd "$snap" && "$tool" --strict --config-data "$BLESS_YAML_CONFIG" -- "${BLESS_FILES[@]}") ||
    return "$BLESS_FAILED"
}

# Summary: Refuse staged JSON that does not parse.
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

# Summary: Check every staged JSON Schema against its metaschema.
bless_check_schemas() {
  local snap=$2 list=$3 python=$4 tool
  bless_read_files < <(bless_select "$list" 'schemas/*.json')
  ((${#BLESS_FILES[@]})) || return 0
  tool=$(bless_tool check-jsonschema "$python") || return
  (cd "$snap" && "$tool" --check-metaschema "${BLESS_FILES[@]}") || return "$BLESS_FAILED"
}

# Summary: Run gate-ci-skills-endpoints on the index (target-file keys).
bless_check_endpoints() {
  local root=$1 snap=$2 python=$4 gate=$2/gates/gate-ci-skills-endpoints.py
  [[ -f $gate ]] || return 0
  "$python" "$gate" --root "$root" --staged || return "$BLESS_FAILED"
}

# Summary: Run every check on the staged bytes, or list them with --dry-run.
# Arguments: repository root, dry run (true or false).
# Stdout: one line per check. Returns: 0 pass, 1 a check failed, blocked or
# usage codes from runtime.bash.
bless_run() {
  local root=$1 dry_run=$2 work status=0
  git -C "$root" rev-parse --git-dir >/dev/null 2>&1 ||
    ci_fail "$CI_EXIT_BLOCKED" "$root is not a git checkout" 'Run bless.sh inside the repository.' ||
    return
  work=$(mktemp -d "${TMPDIR:-/tmp}/bless.XXXXXX") ||
    ci_fail "$CI_EXIT_BLOCKED" 'cannot create a working directory' 'Free disk space, then commit again.' ||
    return
  # Removed on any exit, an interrupted commit included.
  BLESS_WORK=$work
  trap 'rm -rf -- "${BLESS_WORK:?}"' EXIT
  bless_run_in "$root" "$work" "$dry_run" || status=$?
  return "$status"
}

bless_run_in() {
  local root=$1 work=$2 dry_run=$3 list=$2/staged snap=$2/index python check status failed=0
  git -C "$root" diff --cached --name-only --diff-filter=ACMR -z >"$list" || return "$CI_EXIT_BLOCKED"
  if [[ ! -s $list ]]; then
    printf 'bless: nothing staged\n'
    return 0
  fi
  git -C "$root" checkout-index --all --prefix="$snap/" || return "$CI_EXIT_BLOCKED"
  if [[ $dry_run == true ]]; then
    printf 'bless: dry run; the staged paths are:\n'
    tr '\0' '\n' <"$list"
    printf 'bless: checks: %s\n' "$BLESS_CHECKS"
    return 0
  fi
  python=$(bless_python "$root") || return
  for check in $BLESS_CHECKS; do
    status=0
    "bless_check_$check" "$root" "$snap" "$list" "$python" || status=$?
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
