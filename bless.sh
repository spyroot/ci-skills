#!/usr/bin/env bash
# this main bless place-holder template script for all bless actions.
# on commit this that be wired to pre-hook.
#
# Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -Eeuo pipefail

GALILEO_ROOT="$({
	cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
	pwd -P
})"
readonly REPO_ROOT

# shellcheck source=automation/lib/core/exit_codes.bash
source "${REPO_ROOT}/automation/lib/core/exit_codes.bash"
# shellcheck source=automation/lib/core/subject.bash
source "${REPO_ROOT}/automation/lib/core/subject.bash"
# shellcheck source=automation/lib/lint/secret_paths.bash
source "${REPO_ROOT}/automation/lib/lint/secret_paths.bash"
# shellcheck source=automation/lib/lint/git_diff.bash
source "${REPO_ROOT}/automation/lib/lint/git_diff.bash"
# shellcheck source=automation/lib/lint/gitleaks.bash
source "${REPO_ROOT}/automation/lib/lint/gitleaks.bash"
# shellcheck source=automation/lib/lint/markdown.bash
source "${REPO_ROOT}/automation/lib/lint/markdown.bash"
# shellcheck source=automation/lib/lint/shellcheck.bash
source "${REPO_ROOT}/automation/lib/lint/shellcheck.bash"
# shellcheck source=automation/lib/lint/source_graph.bash
source "${REPO_ROOT}/automation/lib/lint/source_graph.bash"
# shellcheck source=automation/lib/lint/script_interface.bash
source "${REPO_ROOT}/automation/lib/lint/script_interface.bash"
# shellcheck source=automation/lib/lint/json.bash
source "${REPO_ROOT}/automation/lib/lint/json.bash"
# shellcheck source=automation/lib/lint/yaml.bash
source "${REPO_ROOT}/automation/lib/lint/yaml.bash"
# shellcheck source=automation/lib/lint/value_secret_stamps.bash
source "${REPO_ROOT}/automation/lib/lint/value_secret_stamps.bash"
# shellcheck source=automation/lib/lint/domain_lock.bash
source "${REPO_ROOT}/automation/lib/lint/domain_lock.bash"
# shellcheck source=automation/lib/lint/helm_lint.bash
source "${REPO_ROOT}/automation/lib/lint/helm_lint.bash"
# shellcheck source=automation/lib/lint/kubernetes_schema.bash
source "${REPO_ROOT}/automation/lib/lint/kubernetes_schema.bash"
# shellcheck source=automation/lib/lint/architecture_schema.bash
source "${REPO_ROOT}/automation/lib/lint/architecture_schema.bash"

usage() {
	printf '%s\n' \
		'Usage: ./bless.sh [--staged] [--dry-run]' \
		'       ./bless.sh --help'
}

main() {
	local dry_run=false
	while (($# > 0)); do
		case "$1" in
		--staged) ;;
		--dry-run) dry_run=true ;;
		--help)
			usage
			return "${GALILEO_EXIT_OK}"
			;;
		*)
			usage >&2
			return "${GALILEO_EXIT_USAGE}"
			;;
		esac
		shift
	done

    # handle 5, cases
    #    - case 1: mac os local execution on pre-commit
    #    - case 2: Same logic, but we execute inside a docker and test and validate.
    #    - case 3: We run runner and ci.
    #    - case 4: We need do hosted runner on github actions.
    #    - case 5: We run github action but on normal github runner. (which is default maxed to 4)
	local maximum="${MAX_JOBS:-4}" available jobs
	available="$(toolchain_cpu_count)" || return


	jobs="$(toolchain_job_count "${maximum}" "${available}")" || return
	printf 'Bless jobs: MAX_JOBS=%s available_cpus=%s effective_jobs=%s\n' \
		"${maximum}" "${available}" "${jobs}"
	export JOBS="${jobs}"

	require_staged_subject "${REPO_ROOT}" || return $?
	block_staged_secret_paths "${REPO_ROOT}" || return $?
	lint_staged_git_diff "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_secrets "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_markdown "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_shell "${REPO_ROOT}" "${dry_run}" || return $?
	# Whole tree, not the staged set: a cycle is a property of the graph, and
	# the commit that closes one usually touches only one of its edges.
	if galileo_source_graph_cycles "${REPO_ROOT}"; then
		printf 'Source graph: acyclic\n'
	else
		printf 'BLOCKER: the library source graph has a cycle\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi
	# Bash has one namespace, so the file is the only separation there is: two
	# files defining one name is whichever was sourced last, silently.
	if galileo_source_graph_duplicate_functions "${REPO_ROOT}"; then
		printf 'Source graph: every sourced function name is defined once\n'
	else
		printf 'BLOCKER: a function name is defined in more than one library file\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi
	if galileo_source_graph_tests_source_two_scripts "${REPO_ROOT}"; then
		printf 'Source graph: no test sources two executables\n'
	else
		printf 'BLOCKER: a test sources two executables into one shell\n' >&2
		return "${GALILEO_EXIT_INVALID_DATA}"
	fi

	lint_staged_script_interface "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_json "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_yaml "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_value_secret_stamps "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_domain_lock "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_architecture_schema "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_helm_chart "${REPO_ROOT}" "${dry_run}" || return $?
	render_staged_helm_chart "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_kubernetes_schema "${REPO_ROOT}" "${dry_run}" || return $?
	lint_staged_kubernetes_policy "${REPO_ROOT}" "${dry_run}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi
