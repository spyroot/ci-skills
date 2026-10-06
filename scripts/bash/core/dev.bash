#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
# shellcheck source=ci-skills/lib/bash/core/runtime.bash
source "${BASH_SOURCE[0]%/*}/../../../ci-skills/lib/bash/core/runtime.bash"
# shellcheck source=scripts/bash/core/toolchain.bash
source "${BASH_SOURCE[0]%/*}/toolchain.bash"
# shellcheck source=scripts/bash/core/hooks.bash
source "${BASH_SOURCE[0]%/*}/hooks.bash"

# Summary: Explain the repository development installer interface.
# Stdout: human help with every accepted option.
# Returns: 0.
ci_dev_help() {
	cat <<'HELP'
Summary: Prepare the ci-skills development toolchain and Git hook (audience: agent and human).

Examples:
  Inspect the complete install plan without changing tools or hooks:
  scripts/dev.sh install --dry-run
  Apply the reviewed install plan:
  scripts/dev.sh install --apply --confirm-install PLAN_FINGERPRINT

Options:
  install|toolchain|conda|hooks  Select one development operation.
  --dry-run                   Show the plan; default.
  --apply --confirm-install SHA256  Execute the reviewed plan.
  --timeout SECONDS           Bound each install step; default 600.
  --log-format text|json      Select diagnostic format; default text.
  --log-level debug|info|warning|error  Minimum diagnostic level; default info.
  --log-file PATH             Append diagnostics to a file.
  --run-id ID                 Attach a caller run ID to diagnostics.
  --describe                 Show machine help and paired schema paths.
  --help                     Show this help.

Output modes:
  human  One summary with action details; default.
  --json  One structured result.
  --yaml  YAML 1.2-compatible structured result.

Usage:
  scripts/dev.sh OPERATION [--dry-run|--apply --confirm-install SHA256] [options]
  scripts/dev.sh --describe
  scripts/dev.sh --help
HELP
}

# Summary: Bind a setup plan to its operation, observed state, and implementation inputs.
# Arguments: $1: repository root; $2: operation; $3: observed plan text.
# Stdout: SHA-256 fingerprint.
# Stderr: hash or file diagnostics.
# Returns: hash or input-read status.
ci_dev_plan_fingerprint() {
	local root="$1" operation="$2" plan="$3" path
	local -a inputs=(
		Makefile bless.sh environment.yml toolchain-dependencies.json
		scripts/dev.sh scripts/bash/core/dev.bash
		scripts/bash/core/toolchain.bash scripts/bash/core/hooks.bash
		scripts/bash/core/bless.bash scripts/bash/core/source_graph.bash
		ci-skills/lib/bash/core/runtime.bash
	)
	{
		printf '%s\0%s\0' "$operation" "$plan"
		for path in "${inputs[@]}"; do
			printf '%s\0' "$path"
			cat "$root/$path" || return
			printf '\0'
		done
	} | ci_sha256_stdin
}

# Summary: Execute one selected local setup step with a bounded timeout.
# Arguments: $1: root; $2: operation; $3: plan or apply; $4: timeout in seconds.
# Stdout: one plan, no-op, or install observation per step.
# Stderr: tool diagnostics.
# Returns: first failed step status or zero.
# Side effects: apply mode installs missing tools, reconciles Conda, or installs a Git hook.
# Idempotency: each core step reads its current state before applying.
# Cleanup: each step owns its temporary resources.
ci_dev_operation() {
	local root="$1" operation="$2" mode="$3" timeout_seconds="$4" action
	local -a actions=("$operation")
	if [[ "$mode" == plan ]]; then
		case "$operation" in
		toolchain) ci_toolchain_profile "$root" bless plan ;;
		conda) ci_toolchain_environment "$root" ci-skills plan ;;
		hooks) ci_hooks_plan "$root" ;;
		install)
			ci_toolchain_profile "$root" bless plan || return
			ci_toolchain_environment "$root" ci-skills plan || return
			ci_hooks_plan "$root"
			;;
		esac
		return
	fi
	if [[ "$operation" == install ]]; then actions=(toolchain conda hooks); fi
	for action in "${actions[@]}"; do
		case "$action" in
		toolchain)
			timeout "$timeout_seconds" bash -c \
				"source \"\$1\"; ci_toolchain_profile \"\$2\" bless install" _ \
				"$root/scripts/bash/core/toolchain.bash" "$root" || return
			;;
		conda)
			timeout "$timeout_seconds" bash -c \
				"source \"\$1\"; ci_toolchain_environment \"\$2\" ci-skills install" _ \
				"$root/scripts/bash/core/toolchain.bash" "$root" || return
			;;
		hooks) ci_hooks_install "$root" || return ;;
		esac
	done
}

# Summary: Render the final local setup result and a repair action on failure.
# Arguments: $1: output mode; $2: operation; $3: plan or apply; $4: status;
#   $5: detail text; $6: safe next step; $7: plan fingerprint.
# Stdout: human, JSON, or YAML-compatible result.
# Stderr: none.
# Returns: output renderer status.
ci_dev_result() {
	local format="$1" operation="$2" mode="$3" status="$4" detail="$5" next="$6" fingerprint="${7:-}" result
	if [[ "$format" == human ]]; then
		printf '%s\nci-skills %s %s: %s\n' "$detail" "$operation" "$mode" "$status"
		[[ -z "$fingerprint" ]] || printf 'PLAN_FINGERPRINT: %s\n' "$fingerprint"
		[[ -z "$next" ]] || printf 'SAFE_NEXT_STEP: %s\n' "$next" >&2
		return
	fi
	result="$(jq -n --arg operation "$operation" --arg mode "$mode" --arg status "$status" \
		--arg detail "$detail" --arg next "$next" --arg run "${CI_RUN_ID:-}" \
		--arg fingerprint "$fingerprint" \
		'{kind:"dev_result",schema_version:"1.0",operation:$operation,mode:$mode,
		status:$status,run_id:$run,detail:$detail} +
		(if $next == "" then {} else {safe_next_step:$next} end) +
		(if $fingerprint == "" then {} else {plan_fingerprint:$fingerprint} end)')" || return
	printf '%s\n' "$result"
}

# Summary: Refuse an invalid setup request with a safe next step.
# Arguments: $1: output format; $2: operation; $3: mode; $4: exit code;
#   $5: blocker reason; $6: repair action; $7: plan fingerprint when known.
# Stdout: one failure result when machine output is requested.
# Stderr: blocker and repair action.
# Returns: the supplied failure code.
ci_dev_refuse() {
	local format="$1" operation="$2" mode="$3" code="$4" reason="$5" next="$6"
	printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$reason" "$next" >&2
	ci_dev_result "$format" "$operation" "$mode" FAIL '' "$next" "${7:-}" || return
	return "$code"
}

# Summary: Parse setup options, select the requested step, and return its result.
# Arguments: $1: repository root; remaining arguments: operation and flags.
# Stdout: help or one setup result.
# Stderr: classified diagnostics.
# Returns: 0 on planned or applied; 64 on usage; 69 on missing tools; otherwise step status.
ci_dev_main() {
	local root="$1" operation='' mode=plan format=human timeout_seconds=600
	local confirm='' dry_explicit=false apply_explicit=false detail='' status=PASS next='' code=0
	local plan='' fingerprint=''
	shift
	if [[ "${1:-}" == --help ]]; then
		ci_dev_help
		return
	fi
	if [[ "${1:-}" == --describe ]]; then
		cat "$root/schemas/commands/help/dev.help.json"
		return
	fi
	operation="${1:-}"
	case "$operation" in install | toolchain | conda | hooks) shift ;; *)
		ci_dev_help >&2
		return 64
		;;
	esac
	while (($#)); do
		case "$1" in
		--dry-run)
			mode=plan
			dry_explicit=true
			;;
		--apply)
			mode=apply
			apply_explicit=true
			;;
		--confirm-install)
			shift
			confirm="${1:-}"
			[[ "$confirm" =~ ^[0-9a-f]{64}$ ]] || {
				ci_dev_refuse "$format" "$operation" "$mode" 64 \
					'invalid plan fingerprint' 'Pass the SHA-256 PLAN_FINGERPRINT from the dry-run result.' || return $?
			}
			;;
		--json) format=json ;;
		--yaml) format=yaml ;;
		--timeout)
			shift
			timeout_seconds="${1:-}"
			[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || return 64
			((timeout_seconds <= 3600)) || return 64
			;;
		--log-format)
			shift
			CI_LOG_FORMAT="${1:-}"
			[[ "$CI_LOG_FORMAT" == text || "$CI_LOG_FORMAT" == json ]] || return 64
			;;
		--log-level)
			shift
			CI_LOG_LEVEL="${1:-}"
			case "$CI_LOG_LEVEL" in debug | info | warning | error) ;; *) return 64 ;; esac
			;;
		--log-file)
			shift
			CI_LOG_FILE="${1:-}"
			[[ -n "$CI_LOG_FILE" && "$CI_LOG_FILE" != --* ]] || return 64
			;;
		--run-id)
			shift
			CI_RUN_ID="${1:-}"
			[[ -n "$CI_RUN_ID" && "$CI_RUN_ID" != --* ]] || return 64
			;;
		--help)
			ci_dev_help
			return
			;;
		--describe)
			cat "$root/schemas/commands/help/dev.help.json"
			return
			;;
		*)
			ci_dev_refuse "$format" "$operation" "$mode" 64 \
				'unknown development option' 'Run scripts/dev.sh --help.' || return $?
			;;
		esac
		shift
	done
	if [[ "$dry_explicit" == true && "$apply_explicit" == true ]]; then
		ci_dev_refuse "$format" "$operation" "$mode" 64 \
			'dry-run and apply conflict' 'Choose exactly one execution mode.' || return $?
	fi
	if [[ "$mode" == apply && -z "$confirm" ]]; then
		ci_dev_refuse "$format" "$operation" "$mode" 64 \
			'apply lacks confirmation' 'Run the dry-run, then pass --apply --confirm-install PLAN_FINGERPRINT.' || return $?
	fi
	plan="$(ci_dev_operation "$root" "$operation" plan "$timeout_seconds")" || code=$?
	if ((code)); then
		ci_dev_refuse "$format" "$operation" "$mode" "$code" \
			'plan failed' 'Repair the reported prerequisite and rerun the dry-run.' || return $?
	fi
	fingerprint="$(ci_dev_plan_fingerprint "$root" "$operation" "$plan")" || return 69
	if [[ "$mode" == apply && "$confirm" != "$fingerprint" ]]; then
		ci_dev_refuse "$format" "$operation" "$mode" 64 \
			'plan inputs changed' 'Rerun the dry-run and review its new PLAN_FINGERPRINT.' \
			"$fingerprint" || return $?
	fi
	if [[ "$mode" == apply ]] && ! command -v timeout >/dev/null 2>&1; then
		ci_dev_refuse "$format" "$operation" "$mode" 69 \
			'timeout command is unavailable' 'Run make toolchain to provide coreutils.' || return $?
	fi
	CI_LOG_FORMAT="${CI_LOG_FORMAT:-text}" CI_LOG_LEVEL="${CI_LOG_LEVEL:-info}"
	CI_RUN_ID="${CI_RUN_ID:-}"
	ci_log info dev start "$operation $mode" || return
	if [[ "$mode" == plan ]]; then
		detail="$plan"
	else
		detail="$(ci_dev_operation "$root" "$operation" "$mode" "$timeout_seconds")" || code=$?
	fi
	if ((code)); then
		status=FAIL
		next='Inspect the operation diagnosis, repair the named dependency, and rerun the dry-run plan.'
		ci_log error dev failed "$operation exited $code" || return
	else
		if [[ "$mode" == plan ]]; then status=PLANNED; fi
		ci_log info dev complete "$operation $mode" || return
	fi
	ci_dev_result "$format" "$operation" "$mode" "$status" "$detail" "$next" "$fingerprint" || return
	return "$code"
}
