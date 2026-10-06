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
}

# Summary: Install or verify only a named manifest profile.
# Arguments: $1: repository root; $2: profile name; $3: plan, install, or verify.
# Stdout: one status line per dependency.
# Stderr: missing tool and repair diagnostics.
# Returns: 0 when every selected tool is available, nonzero otherwise.
# Side effects: install mode invokes Homebrew only for missing host tools.
# Idempotency: present dependencies return READY without a package install.
# Cleanup: Homebrew owns its transaction; this function retains no temporary files.
ci_toolchain_profile() {
	local root="$1" profile="$2" mode="$3" manifest="$1/toolchain-dependencies.json"
	local name dependency package
	local -a packages=()
	if ! command -v jq >/dev/null 2>&1; then
		if [[ "$mode" == install && "$(uname -s)" == Darwin ]] && command -v brew >/dev/null 2>&1; then
			brew install jq || return
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
		if [[ "$mode" == install && "$(uname -s)" == Darwin ]] && command -v brew >/dev/null 2>&1; then
			packages=()
			while IFS= read -r package; do packages+=("$package"); done < <(jq -r '.brew[]?' <<<"$dependency")
			(("${#packages[@]}" > 0)) || {
				printf 'No Homebrew source declared for %s\n' "$name" >&2
				return "$CI_EXIT_DATA"
			}
			brew install "${packages[@]}" || return
			if ci_toolchain_present "$root" "$dependency"; then
				printf 'INSTALLED %s\n' "$name"
				continue
			fi
		fi
		printf 'Missing %s from profile %s\n' "$name" "$profile" >&2
		return "$CI_EXIT_BLOCKED"
	done < <(jq -r --arg profile "$profile" '.profiles[$profile].host[]' "$manifest")
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
