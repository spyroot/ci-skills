#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
# shellcheck source=lib/bash/core/runtime.bash
source "${BASH_SOURCE[0]%/*}/runtime.bash"

# Summary: Find the project Conda executable without using a system Python.
# Arguments: none.
# Stdout: executable path.
# Stderr: none.
# Returns: 0 when found, 1 otherwise.
ci_toolchain_conda() {
  if command -v conda >/dev/null 2>&1; then
    command -v conda
    return
  fi
  local candidate
  for candidate in "$HOME/miniconda3/condabin/conda" "$HOME/miniconda3/bin/conda"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# Summary: Check commands and a declared minimum major version.
# Arguments: $1: repository root; $2: dependency JSON object.
# Stdout: none.
# Stderr: none.
# Returns: 0 if present, 1 otherwise.
ci_toolchain_present() {
  local root="$1" dependency="$2" command minimum observed version_file expected
  local -a probe=()
  while IFS= read -r command; do
    command -v "$command" >/dev/null 2>&1 || return 1
  done < <(jq -r '.commands[]' <<<"$dependency")
  minimum="$(jq -r '.minimumMajor // empty' <<<"$dependency")" || return 1
  if [[ -n "$minimum" ]]; then
    command="$(jq -r '.commands[0]' <<<"$dependency")" || return 1
    observed="$("$command" --version 2>/dev/null)" || return 1
    observed="${observed#v}"
    observed="${observed%%.*}"
    [[ "$observed" =~ ^[0-9]+$ ]] && ((observed >= minimum)) || return 1
  fi
  version_file="$(jq -r '.versionFile // empty' <<<"$dependency")" || return 1
  if [[ -n "$version_file" ]]; then
    expected="$(head -1 "$root/$version_file")" || return 1
    command="$(jq -r '.commands[0]' <<<"$dependency")" || return 1
    observed="$("$command" --version 2>/dev/null)" || return 1
    observed="${observed%%$'\n'*}"
    [[ "$observed" == *"v$expected"* || "$observed" == "$expected" ]] || return 1
  fi
  while IFS= read -r command; do probe+=("$command"); done \
    < <(jq -r '.probe[]?' <<<"$dependency")
  if ((${#probe[@]})) && ! "${probe[@]}" >/dev/null 2>&1; then return 1; fi
}

# Summary: Install a declared dependency through the host Linux package manager.
# Arguments: $1: dependency JSON object.
# Returns: 0 after a package-manager install, blocked when no declared route exists.
# Side effects: installs only the packages named by the dependency manifest.
ci_toolchain_install_linux() {
  local dependency="$1" manager='' field='' package
  local -a packages=() privilege=()
  if command -v apt-get >/dev/null 2>&1; then
    manager=apt-get field=apt
  elif command -v dnf >/dev/null 2>&1; then
    manager=dnf field=yum
  elif command -v yum >/dev/null 2>&1; then
    manager=yum field=yum
  else
    return "$CI_EXIT_BLOCKED"
  fi
  while IFS= read -r package; do packages+=("$package"); done \
    < <(jq -r --arg field "$field" '.[$field][]?' <<<"$dependency")
  ((${#packages[@]})) || return "$CI_EXIT_BLOCKED"
  if ((EUID != 0)); then
    command -v sudo >/dev/null 2>&1 || return "$CI_EXIT_BLOCKED"
    privilege=(sudo -n)
  fi
  "${privilege[@]}" "$manager" install -y "${packages[@]}"
}

# Summary: Install or verify only a named manifest profile.
# Arguments: $1: repository root; $2: profile name; $3: plan, install, or verify.
# Stdout: one status line per dependency.
# Stderr: missing tool and repair diagnostics.
# Returns: 0 when every selected tool is available, nonzero otherwise.
# Side effects: install mode invokes declared host package managers for missing tools.
# Idempotency: present dependencies return READY without a package install.
# Cleanup: Homebrew owns its transaction; this function retains no temporary files.
ci_toolchain_profile() {
  local root="$1" profile="$2" mode="$3" manifest="$1/toolchain-dependencies.json"
  local name dependency package platform
  local -a packages=()
  platform="$(uname -s)"
  if ! command -v jq >/dev/null 2>&1; then
    if [[ "$mode" == install && ("$platform" == Darwin || "$platform" == Linux) ]] &&
      command -v brew >/dev/null 2>&1; then
      brew install jq || return
    elif [[ "$mode" == install && "$platform" == Linux ]]; then
      local manager=''
      local -a privilege=()
      if command -v apt-get >/dev/null 2>&1; then
        manager=apt-get
      elif command -v dnf >/dev/null 2>&1; then
        manager=dnf
      elif command -v yum >/dev/null 2>&1; then
        manager=yum
      fi
      [[ -n "$manager" ]] || return "$CI_EXIT_BLOCKED"
      if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 || return "$CI_EXIT_BLOCKED"
        privilege=(sudo -n)
      fi
      "${privilege[@]}" "$manager" install -y jq || return
      command -v jq >/dev/null 2>&1 || return "$CI_EXIT_BLOCKED"
    else
      printf 'Missing jq; install jq before reading %s\n' "$manifest" >&2
      return "$CI_EXIT_BLOCKED"
    fi
  fi
  jq -e --arg profile "$profile" '
		. as $manifest |
		($manifest.profiles[$profile].host | type) == "array" and
		all($manifest.profiles[$profile].host[]; . as $name |
			any($manifest.dependencies[]; .name == $name))
	' "$manifest" >/dev/null || return "$CI_EXIT_DATA"
  while IFS= read -r name; do
    dependency="$(jq -c --arg name "$name" '.dependencies[] | select(.name == $name)' "$manifest")" || return
    if ci_toolchain_present "$root" "$dependency"; then
      printf 'READY %s\n' "$name"
      continue
    fi
    if [[ "$mode" == plan ]]; then
      printf 'PLAN install %s\n' "$name"
      continue
    fi
    if [[ "$mode" == install && ("$platform" == Darwin || "$platform" == Linux) ]] &&
      command -v brew >/dev/null 2>&1; then
      packages=()
      while IFS= read -r package; do packages+=("$package"); done < <(jq -r '.brew[]?' <<<"$dependency")
      (("${#packages[@]}" > 0)) || {
        printf 'No Homebrew source declared for %s\n' "$name" >&2
        return "$CI_EXIT_DATA"
      }
      if [[ -n "$(brew list --versions "${packages[0]}" 2>/dev/null)" ]] &&
        jq -e 'has("probe")' <<<"$dependency" >/dev/null; then
        brew upgrade "${packages[@]}" || return
      else
        brew install "${packages[@]}" || return
      fi
      if ci_toolchain_present "$root" "$dependency"; then
        printf 'INSTALLED %s\n' "$name"
        continue
      fi
    elif [[ "$mode" == install && "$platform" == Linux ]]; then
      if ci_toolchain_install_linux "$dependency" &&
        ci_toolchain_present "$root" "$dependency"; then
        printf 'INSTALLED %s\n' "$name"
        continue
      fi
    fi
    printf 'Missing %s from profile %s\n' "$name" "$profile" >&2
    printf 'SAFE_NEXT_STEP: Provide a declared package-manager source or the tool, then rerun make install.\n' >&2
    return "$CI_EXIT_BLOCKED"
  done < <(jq -r --arg profile "$profile" '.profiles[$profile].host[]' "$manifest")
}

# Summary: Resolve a repository-local skill destination without following parent symlinks.
# Arguments: $1: repository root; $2: relative destination path.
# Stdout: physical destination path.
# Returns: 0 for a contained directory, invalid-data status otherwise.
ci_toolchain_skill_destination() {
  local root="$1" relative="$2" component current
  current="$(cd -P -- "$root" && pwd -P)" || return "$CI_EXIT_DATA"
  [[ "$relative" != /* ]] || return "$CI_EXIT_DATA"
  local -a components=()
  IFS=/ read -r -a components <<<"$relative"
  for component in "${components[@]}"; do
    [[ -n "$component" && "$component" != . && "$component" != .. ]] ||
      return "$CI_EXIT_DATA"
    current="$current/$component"
    [[ ! -L "$current" ]] || return "$CI_EXIT_DATA"
    [[ ! -e "$current" || -d "$current" ]] || return "$CI_EXIT_DATA"
  done
  printf '%s\n' "$current"
}

# Summary: Remove an uncommitted skill only when its owner marker still matches.
# Arguments: $1: installed skill directory.
# Returns: 0 when no owned partial target remains.
ci_toolchain_cleanup_skill_target() {
  local target="$1" marker="$1/.ci-skills-owner"
  if [[ -f "$marker" && ! -L "$marker" ]] &&
    [[ "$(cat "$marker")" == "$CI_SKILL_OWNER" ]]; then
    ci_runtime_cleanup_dir "$target"
  fi
}

# Summary: Compare an installed skill with the bundled source and require both commands to succeed.
# Arguments: $1: skill name; $2: SKILL.md path.
# Returns: 0 only for a successful, exact byte match.
ci_toolchain_skill_matches() (
  set -o pipefail
  glab skills get "$1" | cmp -s - "$2"
)

# Summary: Stage, verify, and atomically promote one bundled skill.
# Arguments: $1: skill name; $2: validated destination directory.
# Returns: 0 only when the final SKILL.md matches the bundled source.
# Cleanup: removes staged files and an owned, uncommitted promoted directory.
ci_toolchain_install_skill() (
  local name="$1" destination="$2" temporary target="$2/$1" marker
  ci_runtime_lifecycle_begin || return
  mkdir -p -- "$destination" || return
  temporary="$(mktemp -d "$destination/.ci-skills-$name.XXXXXX")" || return
  ci_runtime_cleanup_push ci_runtime_cleanup_dir "$temporary" || return
  glab skills install "$name" --path "$temporary" || return
  [[ -d "$temporary/$name" && ! -L "$temporary/$name" ]] || return "$CI_EXIT_FAILED"
  [[ -f "$temporary/$name/SKILL.md" && ! -L "$temporary/$name/SKILL.md" ]] ||
    return "$CI_EXIT_FAILED"
  ci_toolchain_skill_matches "$name" "$temporary/$name/SKILL.md" ||
    return "$CI_EXIT_FAILED"
  marker="$temporary/$name/.ci-skills-owner"
  CI_SKILL_OWNER="$temporary"
  printf '%s\n' "$CI_SKILL_OWNER" >"$marker" || return
  ci_runtime_cleanup_push ci_toolchain_cleanup_skill_target "$target" || return
  [[ ! -e "$target" && ! -L "$target" ]] || return "$CI_EXIT_CONFLICT"
  mv -n -- "$temporary/$name" "$target" || return
  [[ -f "$target/.ci-skills-owner" ]] &&
    [[ "$(cat "$target/.ci-skills-owner")" == "$CI_SKILL_OWNER" ]] ||
    return "$CI_EXIT_CONFLICT"
  ci_toolchain_skill_matches "$name" "$target/SKILL.md" || return "$CI_EXIT_FAILED"
  rm -f -- "$target/.ci-skills-owner" || return
  printf 'INSTALLED %s\n' "$name"
)

# Summary: Install declared bundled agent skills into this checkout and verify their bytes.
# Arguments: $1: repository root; $2: profile name; $3: plan or install.
# Stdout: one plan, ready, or installed line per skill.
# Stderr: missing glab, unsafe destination, or read-back diagnostics.
# Returns: 0 when every declared skill matches glab; blocked or conflict otherwise.
# Side effects: install mode writes declared skills below the profile directory.
# Idempotency: matching installed skills are left unchanged.
ci_toolchain_bundled_skills() {
  local root="$1" profile="$2" mode="$3" manifest="$1/toolchain-dependencies.json"
  local relative destination name skill_file skill_dir provider
  if [[ "$mode" != plan ]] && ! command -v glab >/dev/null 2>&1; then
    printf 'glab is missing; install the agent host profile first.\n' >&2
    return "$CI_EXIT_BLOCKED"
  fi
  relative="$(jq -er --arg profile "$profile" '.profiles[$profile].skillsDirectory' "$manifest")" ||
    return "$CI_EXIT_DATA"
  destination="$(ci_toolchain_skill_destination "$root" "$relative")" || {
    printf 'Unsafe agent skills directory in %s\n' "$manifest" >&2
    return "$CI_EXIT_DATA"
  }
  provider="$(jq -c '.dependencies[] | select(.name == "glab")' "$manifest")" ||
    return "$CI_EXIT_DATA"
  [[ -n "$provider" ]] || return "$CI_EXIT_DATA"
  while IFS= read -r name; do
    [[ "$name" =~ ^[a-z][a-z0-9-]*$ ]] || return "$CI_EXIT_DATA"
    skill_dir="$destination/$name"
    skill_file="$skill_dir/SKILL.md"
    if [[ -L "$skill_dir" || -L "$skill_file" ]]; then
      printf 'Unsafe agent skill path: %s\n' "$skill_dir" >&2
      return "$CI_EXIT_CONFLICT"
    fi
    if [[ -e "$skill_dir" ]]; then
      if [[ "$mode" == plan ]] && ! ci_toolchain_present "$root" "$provider"; then
        printf 'PLAN verify bundled skill %s after glab upgrade\n' "$name"
        continue
      fi
      if [[ -d "$skill_dir" && -f "$skill_file" ]] &&
        ci_toolchain_skill_matches "$name" "$skill_file"; then
        printf 'READY %s\n' "$name"
        continue
      fi
      printf 'Existing agent skill differs from bundled %s: %s\n' "$name" "$skill_file" >&2
      return "$CI_EXIT_CONFLICT"
    fi
    if [[ "$mode" == plan ]]; then
      printf 'PLAN install bundled skill %s into %s\n' "$name" "$destination"
      continue
    fi
    ci_toolchain_skill_destination "$root" "$relative" >/dev/null || return "$CI_EXIT_DATA"
    ci_toolchain_install_skill "$name" "$destination" || return
  done < <(jq -r --arg profile "$profile" '.profiles[$profile].skills[]' "$manifest")
}

# Summary: Create or update the environment declared by environment.yml.
# Arguments: $1: repository root; $2: environment name; $3: plan or install.
# Stdout: Conda status.
# Stderr: Conda diagnostics.
# Returns: Conda status.
# Side effects: install mode creates or updates the named Conda environment.
# Idempotency: an environment with all bless tools returns NO_OP.
# Cleanup: Conda owns its transaction; this function retains no temporary files.
ci_toolchain_environment() {
  local root="$1" expected="$2" mode="${3:-install}" conda_bin declared tool
  declared="$(sed -n 's/^name:[[:space:]]*//p' "$root/environment.yml" | head -1)"
  [[ "$declared" == "$expected" ]] || {
    printf 'Conda environment name mismatch: expected %s, declared %s\n' "$expected" "$declared" >&2
    return "$CI_EXIT_DATA"
  }
  if [[ "$mode" == plan ]]; then
    printf 'PLAN create or update Conda environment %s from environment.yml\n' "$expected"
    return 0
  fi
  conda_bin="$(ci_toolchain_conda)" || {
    printf 'Conda is missing; install Miniforge before make install.\n' >&2
    return "$CI_EXIT_BLOCKED"
  }
  if "$conda_bin" env list --json | jq -e --arg name "$expected" '
		any(.envs[]; (split("/") | last) == $name)
	' >/dev/null; then
    local ready=1
    while IFS= read -r tool; do
      if ! "$conda_bin" run -n "$expected" "$tool" --version >/dev/null 2>&1; then
        ready=0
        break
      fi
    done < <(jq -r '.profiles.bless.conda[]' "$root/toolchain-dependencies.json")
    if ((ready)); then
      printf 'NO_OP Conda environment %s already provides bless tools\n' "$expected"
      return 0
    fi
    "$conda_bin" env update --name "$expected" --file "$root/environment.yml"
  else
    "$conda_bin" env create --name "$expected" --file "$root/environment.yml"
  fi
  while IFS= read -r tool; do
    "$conda_bin" run -n "$expected" "$tool" --version >/dev/null || return
  done < <(jq -r '.profiles.bless.conda[]' "$root/toolchain-dependencies.json")
}
