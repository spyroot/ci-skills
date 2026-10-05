#!/usr/bin/env bash

[[ "${CI_RESULT_LOADED:-0}" == 1 ]] && return 0
readonly CI_RESULT_LOADED=1

# this one we need to make robust
CI_RESULT_ROOT="$({
	cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." || return
	pwd -P
})"
readonly CI_RESULT_ROOT

# shellcheck source=automation/lib/core/exit_codes.bash
source "${CI_RESULT_ROOT}/automation/lib/core/exit_codes.bash"

_ci_result_valid_var_name() {
	[[ "$1" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]
}

# Summary: Append one check result to a caller-owned Bash array.
# Arguments: array variable, check, status, detail, and optional numeric count.
# Environment inputs: jq on PATH.
# Stdout/stderr: none unless jq fails.
# Exit classes: usage, failure, or success.
# Side effects: appends to the named array; idempotency is caller-owned.
ci_result_add() {
	(($# == 4 || $# == 5)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" check="$2" status="$3" detail="$4" count="${5:-}"
	local item
	_CI_result_valid_var_name "${array_name}" ||
		return "${CI_EXIT_USAGE}"
	if [[ -n "${count}" ]]; then
		item="$(
			jq -nc \
				--arg check "${check}" \
				--arg status "${status}" \
				--arg detail "${detail}" \
				--argjson count "${count}" \
				'{check: $check, status: $status, detail: $detail, count: $count}'
		)" || return "${CI_EXIT_FAILURE}"
	else
		item="$(
			jq -nc \
				--arg check "${check}" \
				--arg status "${status}" \
				--arg detail "${detail}" \
				'{check: $check, status: $status, detail: $detail}'
		)" || return "${CI_EXIT_FAILURE}"
	fi
	eval "${array_name}+=(\"\${item}\")"
}

#  Append one check result whose evidence is the sanitized lines of a
#   captured output file, such as the stdout of a gate that passed.
# Arguments: array variable, check, status, detail, and captured output path.
# Environment inputs: jq on PATH.
# Stdout/stderr: none unless jq fails.
# Exit classes: usage, missing file, failure, or success.
# Side effects: appends to the named array; idempotency is caller-owned.
#
# Sanitized means safe to embed in a JSON report and to read in a CI log. The
# input is rejected when it resembles a credential, authentication header,
# private key, kubeconfig, or raw Kubernetes Secret. CSI escape sequences
# (color and cursor codes) are removed, then every control character other than
# tab, and blank lines are dropped. jq reads the file itself, so output of any
# size stays out of the argument list.
# Environment inputs: jq and grep on PATH.
_ci_result_contains_secret_like_input() {
	LC_ALL=C grep -Eiq \
		-e '(^|[[:space:]])(authorization|proxy-authorization|private-token|job-token|x-auth-token|cookie|set-cookie)[[:space:]]*:' \
		-e '(^|[^[:alnum:]_])(access[_-]?token|refresh[_-]?token|token|password|passwd|api[_-]?key|client[_-]?secret|private[_-]?key)[[:space:]]*[:=][[:space:]]*[^[:space:]]+' \
		-e '[[:alpha:]][[:alnum:]+.-]*://[^/@[:space:]]+:[^/@[:space:]]+@' \
		-e '-----BEGIN ([A-Z0-9 ]+ )?PRIVATE KEY-----' \
		-e '^[[:space:]-]*kind:[[:space:]]*Secret([[:space:]#]|$)' \
		-e '["]kind["][[:space:]]*:[[:space:]]*["]Secret["]' \
		-e '^[[:space:]]*(current-context|client-certificate-data|client-key-data)[[:space:]]*:'
}

# whether a file's contents look like a credential.
# Arguments: path to the file under consideration.
# Environment inputs: PATH selects grep.
# Stdout/stderr: none - the contents are never echoed.
# Exit classes: usage, missing file, success when it looks like a credential,
#   failure when it does not.
# Side effects: none. Idempotency: pure. Cleanup: none.
#
# The public entry point to the detector directly above, which until now
# classified provider stderr and nothing else. Callers outside this file -
# the agent disclosure policy is the first - need the same verdict before they
# publish text, and a second copy of that pattern set would drift from this one.
ci_result_text_is_secret_like() {
	(($# == 1)) || return "${CI_EXIT_USAGE}"
	[[ -r "$1" ]] || return "${CI_EXIT_MISSING_FILE}"
	_ci_result_contains_secret_like_input <"$1"
}

# Summary: Classify captured provider stderr without publishing its contents.
# Arguments: captured stderr path.
# Stdout: one stable, non-secret classification.
ci_result_classify_provider_error() {
	(($# == 1)) || return "${CI_EXIT_USAGE}"
	[[ -r "$1" ]] || return "${CI_EXIT_MISSING_FILE}"
	if _CI_result_contains_secret_like_input <"$1"; then
		printf 'redacted-sensitive-output\n'
	elif grep -Eiq 'forbidden|unauthori[sz]ed|permission denied' "$1"; then
		printf 'authorization\n'
	elif grep -Eiq 'deadline exceeded|timed out|timeout' "$1"; then
		# Ordered before the quota test, whose pattern also matches "exceeded".
		# helm returns "context deadline exceeded" for a wait timeout; matched
		# by quota first it is classified quota-or-capacity.
		printf 'transport\n'
	elif grep -Eiq 'quota|exceeded|insufficient' "$1"; then
		printf 'quota-or-capacity\n'
	elif grep -Eiq 'admission|denied by|policy|securitycontext' "$1"; then
		printf 'admission-policy\n'
	elif grep -Eiq 'invalid|malformed|cannot unmarshal|failed to decode' "$1"; then
		printf 'invalid-manifest\n'
	elif grep -Eiq 'timeout|timed out|deadline|connection|network|tls|x509' "$1"; then
		printf 'transport\n'
	else
		printf 'provider-rejected\n'
	fi
}

CI_result_add_evidence() {
	(($# == 5)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" check="$2" status="$3" detail="$4" path="$5"
	local item
	_CI_result_valid_var_name "${array_name}" ||
		return "${CI_EXIT_USAGE}"
	[[ -r "${path}" ]] || return "${CI_EXIT_MISSING_FILE}"
	if printf '%s\n' "${detail}" | _CI_result_contains_secret_like_input ||
		_CI_result_contains_secret_like_input <"${path}"; then
		return "${CI_EXIT_BLOCKED}"
	fi
	item="$(
		jq -nc \
			--arg check "${check}" \
			--arg status "${status}" \
			--arg detail "${detail}" \
			--rawfile output "${path}" \
			'{check: $check, status: $status, detail: $detail,
			  evidence: ($output |
			    gsub("\\x1B\\[[0-?]*[ -/]*[@-~]"; "") |
			    gsub("[\\x00-\\x08\\x0B-\\x1F\\x7F]"; "") |
			    split("\n") | map(select(test("\\S"))))}'
	)" || return "${CI_EXIT_FAILURE}"
	eval "${array_name}+=(\"\${item}\")"
}

_CI_result_stream() {
	(($# == 1)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" count item index
	_CI_result_valid_var_name "${array_name}" ||
		return "${CI_EXIT_USAGE}"
	eval "count=\${#${array_name}[@]}"
	for ((index = 0; index < count; index++)); do
		eval "item=\${${array_name}[${index}]}"
		printf '%s\n' "${item}"
	done
}

# Summary: Render check results with the standard Galileo result envelope.
# Arguments: array variable, schema path, and kind.
# Environment inputs: jq on PATH.
# Stdout: JSON result object.
# Side effects/idempotency/cleanup: read-only and repeatable.
CI_result_render_json() {
	(($# == 3)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" schema="$2" kind="$3"
	_CI_result_stream "${array_name}" |
		jq -s --arg schema "${schema}" --arg kind "${kind}" '{
			"$schema": $schema,
			apiVersion: "internal.spyroot.dev/v1alpha1",
			kind: $kind,
			status: (if ([.[] | select(.status != "READY")] | length) == 0
				then "READY"
				else "ACTION_REQUIRED"
				end),
			checks: .
		}'
}

CI_result_render_yaml() {
	CI_result_render_json "$@" | yq eval -P -
}

CI_result_render_text() {
	CI_result_render_json "$@" |
		jq -r '.checks[] |
			if has("count") then
				"\(.status)\t\(.check)\tcount=\(.count)\t\(.detail)"
			else
				"\(.status)\t\(.check)\t\(.detail)"
			end'
}

# Summary: Render check results in the output mode the caller asked for.
# Arguments: array variable, schema path, kind, and text|json|yaml.
# Environment inputs: jq on PATH; yq as well for yaml.
# Stdout: the standard Galileo result envelope in that mode.
# Stderr: one blocker line naming an unknown mode.
# Exit classes: usage for an unknown mode, otherwise the renderer's status.
# Side effects: none. Idempotency: read-only and repeatable. Cleanup: none.
#
# Every result-producing library reached for the same three-way case. One copy
# means a fourth output mode, or a change to how an unknown one is refused,
# lands once instead of in every caller.
CI_result_render() {
	(($# == 4)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" schema="$2" kind="$3" output="$4"
	case "${output}" in
	json) CI_result_render_json "${array_name}" "${schema}" "${kind}" ;;
	yaml) CI_result_render_yaml "${array_name}" "${schema}" "${kind}" ;;
	text) CI_result_render_text "${array_name}" "${schema}" "${kind}" ;;
	*)
		printf 'BLOCKER: unknown output mode: %s\n' "${output}" >&2
		return "${CI_EXIT_USAGE}"
		;;
	esac
}

CI_result_exit_status() {
	if CI_result_render_json "$@" |
		jq -e '([.checks[] | select(.status != "READY")] | length) == 0' \
			>/dev/null; then
		return "${CI_EXIT_OK}"
	fi
	return "${CI_EXIT_BLOCKED}"
}

# Summary: Read one value from a YAML index without treating stale files as live proof.
# Arguments: index path and yq expression.
# Environment inputs: yq on PATH.
# Stdout: resolved string or empty string.
# Exit classes: always success; callers decide whether the empty value blocks.
CI_index_value() {
	(($# == 2)) || return "${CI_EXIT_USAGE}"
	local index_file="$1" query="$2" value=''
	if command -v yq >/dev/null 2>&1 && [[ -r "${index_file}" ]]; then
		value="$(
			yq eval -r "${query} // \"\"" "${index_file}" 2>/dev/null ||
				true
		)"
	fi
	[[ "${value}" != null ]] || value=''
	printf '%s' "${value}"
}

# Resolve a config value from an environment override or YAML index.
# Arguments: result array, index path, env name, yq expression, output variable.
# Environment inputs: named env var and yq on PATH.
# Exit classes: usage, missing value, assignment failure, or success.
ci_config_value() {
	(($# == 5)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" index_file="$2" env_name="$3" query="$4" output_var="$5"
	local _CI_config_resolved_value
	_CI_result_valid_var_name "${output_var}" ||
		return "${CI_EXIT_USAGE}"
	_CI_config_resolved_value="${!env_name:-}"
	if [[ -z "${_CI_config_resolved_value}" ]]; then
		_CI_config_resolved_value="$(
			CI_index_value "${index_file}" "${query}"
		)"
	fi
	if [[ -z "${_CI_config_resolved_value}" ]]; then
		CI_result_add "${array_name}" "config.${env_name}" \
			"MISSING_VALUE" "${env_name} is missing from the environment and supplied configuration"
		return "${CI_EXIT_MISSING_VALUE}"
	fi
	printf -v "${output_var}" '%s' "${_CI_config_resolved_value}" ||
		return "${CI_EXIT_FAILURE}"
}

# Record missing tools into a caller-owned result array.
# Arguments: result array followed by one or more executable names.
# Environment inputs: PATH.
# Exit classes: success when every tool exists, failure when one or more is absent.
ci_need_tools() {
	(($# >= 2)) || return "${CI_EXIT_USAGE}"
	local array_name="$1" tool missing=0
	shift
	for tool in "$@"; do
		if ! command -v "${tool}" >/dev/null 2>&1; then
			CI_result_add "${array_name}" "tool.${tool}" \
				"MISSING_FILE" "${tool} is not on PATH"
			missing=1
		fi
	done
	return "${missing}"
}
