#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
# shellcheck source=scripts/bash/core/toolchain.bash
source "${BASH_SOURCE[0]%/*}/toolchain.bash"
# shellcheck source=scripts/bash/core/source_graph.bash
source "${BASH_SOURCE[0]%/*}/source_graph.bash"

# Summary: Show the public interface of the staged source checker.
# Arguments: none.
# Stdout: usage text.
# Stderr: none.
# Returns: 0.
ci_bless_usage() {
	cat <<'EOF'
Summary: Check staged source before commit (audience: agent and human).

Examples:
  Check the exact staged index before committing:
  ./bless.sh --staged
  Inspect the planned checks without running linters:
  ./bless.sh --staged --dry-run --json

Options:
  --staged   Check exact Git index bytes (default).
  --all      Check tracked working-tree files.
  --dry-run  List selected checks without running linters.
  --describe  Show the paired argument and result schema paths.
  --help     Show help without requiring tools or credentials.

Output modes:
  human  One summary line (default).
  --json  One structured result on stdout.
  --yaml  The same structured result as YAML on stdout.

Usage:
  ./bless.sh [--staged|--all] [--dry-run] [--json|--yaml]
  ./bless.sh --describe
  ./bless.sh --help

Exit 0: passed or planned; 1: a check failed; 64: bad arguments;
69: a required tool is unavailable.
EOF
}

# Summary: Render a classified prerequisite failure in the requested output mode.
# Arguments: $1: human, json, or yaml; $2: failed check; $3: safe next step.
# Stdout: one result in the requested format.
# Stderr: human blocker and repair action.
# Returns: 0 after rendering; the caller returns its own failure status.
ci_bless_blocked() {
	local format="$1" check="$2" next="$3" result
	if [[ "$format" == human ]]; then
		printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$check" "$next" >&2
		return
	fi
	if command -v jq >/dev/null 2>&1; then
		result="$(jq -n --arg check "$check" --arg next "$next" \
			'{kind:"bless_result",schema_version:"1.0",status:"FAIL",scope:"staged",
			summary:{selected_files:0,check_count:1,failed_checks:1},
			checks:[{path:"prerequisite",check:$check,status:"FAIL"}],safe_next_step:$next}')" || return
	else
		result='{"kind":"bless_result","schema_version":"1.0","status":"FAIL","scope":"staged","summary":{"selected_files":0,"check_count":1,"failed_checks":1},"checks":[{"path":"prerequisite","check":"jq","status":"FAIL"}],"safe_next_step":"Run make install to provide jq."}'
	fi
	printf '%s\n' "$result"
	printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$check" "$next" >&2
}

# Summary: Read the selected version of one repository file.
# Arguments: $1: staged or all; $2: repository-relative path.
# Stdout: file bytes.
# Stderr: Git or file diagnostics.
# Returns: reader status.
ci_bless_content() {
	if [[ "$1" == staged ]]; then
		git show ":$2"
	else
		cat -- "./$2"
	fi
}

# Summary: Append one machine-readable check observation.
# Arguments: $1: records path; $2: file path; $3: check name; $4: status.
# Stdout: none.
# Stderr: jq diagnostics.
# Returns: jq status.
ci_bless_record() {
	jq -cn --arg path "$2" --arg check "$3" --arg status "$4" \
		'{path:$path,check:$check,status:$status}' >>"$1"
}

# Summary: Identify operator-local paths that must never enter this public repository.
# Arguments: $1: repository-relative path to classify.
# Returns: 0 for a secret-class path; 1 otherwise.
ci_bless_secret_path() {
	case "$1" in
	.internal/* | .codex/* | .claude/* | .agent-review/* | \
		AGENT*.md | CLAUDE*.md | CODEX*.md | TEAM_GUIDE.md | \
		shared.md | brain-shared.md | *.key | *.pem | *.crt | *.p12)
		return 0
		;;
	esac
	return 1
}

# Summary: Check one file against a linter using the selected file bytes.
# Arguments: $1: records path; $2: scope; $3: file; $4: check name;
#   $5: dry-run flag; remaining arguments: linter command vector.
# Stdout: none.
# Stderr: bounded failure diagnostics.
# Returns: 0 on pass or plan, 1 on check failure.
ci_bless_check() {
	local records="$1" scope="$2" file="$3" label="$4" dry_run="$5" output
	shift 5
	if [[ "$dry_run" == true ]]; then
		ci_bless_record "$records" "$file" "$label" PLANNED
		return
	fi
	if output="$(ci_bless_content "$scope" "$file" | "$@" 2>&1)"; then
		ci_bless_record "$records" "$file" "$label" PASS
	else
		ci_bless_record "$records" "$file" "$label" FAIL
		printf 'FAIL %s %s\n%s\n' "$label" "$file" "${output:0:4096}" >&2
		return 1
	fi
}

# Summary: Select the checks required for one staged or tracked file.
# Arguments: $1: records path; $2: scope; $3: file; $4: dry-run flag;
#   $5: Conda executable; $6: environment name.
# Stdout: none.
# Stderr: check diagnostics.
# Returns: 0 when all selected checks pass, 1 otherwise.
ci_bless_file() {
	local records="$1" scope="$2" file="$3" dry_run="$4" conda_bin="$5" env_name="$6"
	local failed=0
	local yaml_config='{extends: default, rules: {document-start: disable, line-length: {max: 120}, truthy: disable}}'
	case "$file" in
	*.py)
		ci_bless_check "$records" "$scope" "$file" ruff-check "$dry_run" \
			"$conda_bin" run --no-capture-output -n "$env_name" ruff check --no-cache --stdin-filename "$file" - || failed=1
		ci_bless_check "$records" "$scope" "$file" ruff-format "$dry_run" \
			"$conda_bin" run --no-capture-output -n "$env_name" ruff format --check --no-cache \
			--stdin-filename "$file" - || failed=1
		;;
	*.json)
		ci_bless_check "$records" "$scope" "$file" json "$dry_run" jq -e -s 'length == 1' || failed=1
		if [[ "$file" == schemas/*.schema.json ]]; then
			ci_bless_check "$records" "$scope" "$file" json-schema "$dry_run" \
				"$conda_bin" run --no-capture-output -n "$env_name" \
				check-jsonschema --check-metaschema --default-filetype json - || failed=1
		fi
		;;
	*.yaml | *.yml)
		ci_bless_check "$records" "$scope" "$file" yaml "$dry_run" \
			"$conda_bin" run --no-capture-output -n "$env_name" \
			yamllint --strict --config-data "$yaml_config" - || failed=1
		;;
	*.toml)
		ci_bless_check "$records" "$scope" "$file" toml "$dry_run" \
			taplo lint --no-schema - || failed=1
		;;
	*.md | *.markdown)
		ci_bless_check "$records" "$scope" "$file" markdown "$dry_run" \
			markdownlint-cli2 - --config "$CI_BLESS_ROOT/.markdownlint-cli2.yaml" --no-globs || failed=1
		;;
	*.sh | *.bash | .githooks/pre-commit)
		ci_bless_check "$records" "$scope" "$file" shellcheck "$dry_run" \
			shellcheck -s bash -e SC1091 - || failed=1
		ci_bless_check "$records" "$scope" "$file" shfmt "$dry_run" shfmt -d - || failed=1
		;;
	esac
	return "$failed"
}

# Summary: Check the named repository and render a human, JSON, or YAML result.
# Arguments: $1: repository root; remaining arguments: CLI options.
# Stdout: one result.
# Stderr: failures and repair guidance.
# Returns: 0 on pass/plan, 1 on failed check, 64 on usage, 69 on missing tools.
# Side effects: creates and removes a temporary result directory; never rewrites source.
# Cleanup: EXIT removes the temporary result directory.
ci_bless_main() {
	local CI_BLESS_ROOT="$1" scope=staged dry_run=false format=human conda_bin='' env_name=ci-skills
	local file status=PASS failed=0 selected=0 records selection result
	shift
	while (($#)); do
		case "$1" in
		--staged) scope=staged ;;
		--all) scope=all ;;
		--dry-run) dry_run=true ;;
		--json) format=json ;;
		--yaml) format=yaml ;;
		--describe)
			cat "$CI_BLESS_ROOT/schemas/commands/help/bless.help.json"
			return
			;;
		--help)
			ci_bless_usage
			return
			;;
		*)
			ci_bless_usage >&2
			return 64
			;;
		esac
		shift
	done
	cd "$CI_BLESS_ROOT" || return 69
	[[ "$(git rev-parse --show-toplevel 2>/dev/null)" == "$CI_BLESS_ROOT" ]] || {
		printf 'bless.sh must run from its repository root\n' >&2
		return 69
	}
	command -v jq >/dev/null 2>&1 || {
		ci_bless_blocked "$format" jq 'Run make install to provide jq.'
		return 69
	}
	if [[ "$dry_run" == false || "$format" == yaml ]]; then
		conda_bin="$(ci_toolchain_conda)" || {
			ci_bless_blocked "$format" conda 'Run make install to provide the project Conda environment.'
			return 69
		}
	fi
	if [[ "$dry_run" == false ]]; then
		local tool
		for tool in taplo shellcheck shfmt markdownlint-cli2 gitleaks; do
			command -v "$tool" >/dev/null 2>&1 || {
				ci_bless_blocked "$format" "$tool" 'Run make install to provide the bless toolchain.'
				return 69
			}
		done
	fi
	CI_BLESS_TEMP="$(mktemp -d)" || return 69
	trap 'rm -rf -- "$CI_BLESS_TEMP"' EXIT
	records="$CI_BLESS_TEMP/records"
	selection="$CI_BLESS_TEMP/selection"
	: >"$records"
	if [[ "$scope" == staged ]]; then
		git diff --cached --name-only -z --diff-filter=ACMR >"$selection" || return 69
	else
		git ls-files -z >"$selection" || return 69
	fi
	if [[ "$scope" == staged && "$dry_run" == false ]]; then
		if ! git diff --cached --check >&2; then
			ci_bless_record "$records" "staged-index" whitespace FAIL
			failed=1
		else
			ci_bless_record "$records" "staged-index" whitespace PASS
		fi
	fi
	while IFS= read -r -d '' file; do
		[[ "$scope" != all || -f "$file" ]] || continue
		selected=$((selected + 1))
		if ci_bless_secret_path "$file"; then
			ci_bless_record "$records" "$file" secret-path FAIL
			printf 'Refusing secret-class agent file: %s\n' "$file" >&2
			failed=1
			continue
		fi
		ci_bless_file "$records" "$scope" "$file" "$dry_run" "$conda_bin" "$env_name" || failed=1
	done <"$selection"
	if [[ "$dry_run" == true ]]; then
		ci_bless_record "$records" bash-source-graph source-cycle PLANNED
	elif ci_source_graph_acyclic "$scope"; then
		ci_bless_record "$records" bash-source-graph source-cycle PASS
	else
		ci_bless_record "$records" bash-source-graph source-cycle FAIL
		failed=1
	fi
	if [[ "$scope" == staged && "$selected" -gt 0 && "$dry_run" == false ]]; then
		if gitleaks git --staged --no-banner --redact=100 --log-level error >/dev/null; then
			ci_bless_record "$records" staged-index gitleaks PASS
		else
			ci_bless_record "$records" staged-index gitleaks FAIL
			printf 'Staged secret scan failed; inspect the staged diff.\n' >&2
			failed=1
		fi
	fi
	if [[ "$dry_run" == true && "$failed" == 0 ]]; then
		status=PLANNED
	elif [[ "$failed" != 0 ]]; then
		status=FAIL
	fi
	local next=''
	if [[ "$status" == FAIL ]]; then
		next='Inspect the reported check diagnostics, repair the staged content, and rerun make bless.'
	fi
	result="$(jq -s --arg status "$status" --arg scope "$scope" --argjson selected "$selected" --arg next "$next" \
		'{kind:"bless_result",schema_version:"1.0",status:$status,scope:$scope,
			summary:{selected_files:$selected,check_count:length,failed_checks:([.[] | select(.status=="FAIL")] | length)},
			checks:.} + (if $next == "" then {} else {safe_next_step:$next} end)' "$records")" || return 69
	case "$format" in
	json) printf '%s\n' "$result" ;;
	yaml) printf '%s\n' "$result" | "$conda_bin" run --no-capture-output -n "$env_name" \
		python -c 'import json,sys,yaml; yaml.safe_dump(json.load(sys.stdin),sys.stdout,sort_keys=False)' ;;
	human) printf 'Bless %s: %s (%s files, %s checks)\n' "$scope" "$status" \
		"$selected" "$(jq -r '.summary.check_count' <<<"$result")" ;;
	esac
	[[ "$failed" == 0 ]]
}
