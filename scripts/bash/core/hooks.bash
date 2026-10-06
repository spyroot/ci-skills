#!/usr/bin/env bash
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

# Summary: Print the owned pre-commit hook for comparison and installation.
# Stdout: hook source bytes.
# Returns: 0.
ci_hooks_body() {
	cat <<'HOOK'
#!/usr/bin/env bash
# ci-skills-managed-pre-commit-v1
set -Eeuo pipefail
root="$(git rev-parse --show-toplevel)"
if [[ -x "$root/bless.sh" ]] && grep -q 'ci-skills-bless-v1' "$root/bless.sh"; then
	exec "$root/bless.sh" --staged
fi
printf 'ci-skills pre-commit: bless.sh is unavailable in this checkout.\n' >&2
exit 69
HOOK
}

# Summary: Report the hook change required without modifying Git state.
# Arguments: $1: repository root.
# Stdout: PLAN or NO_OP observation.
# Stderr: foreign hook or unavailable dispatcher diagnosis.
# Returns: 0 for installable or already installed; 69 or 73 otherwise.
ci_hooks_plan() {
	local root="$1" common target configured dispatcher
	common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)" || return
	target="$common/hooks/pre-commit"
	configured="$(git -C "$root" config --get core.hooksPath || true)"
	if [[ -n "$configured" ]]; then
		if [[ "$configured" != /* ]]; then configured="$root/$configured"; fi
		dispatcher="$configured/pre-commit"
		# Match the known global dispatch to the common Git hook directory.
		# shellcheck disable=SC2016
		if [[ ! -x "$dispatcher" ]] ||
			! grep -Fq 'git_common_dir="$(git rev-parse --git-common-dir' "$dispatcher" ||
			! grep -Fq 'exec "$local_hook" "$@"' "$dispatcher"; then
			printf 'Configured hooksPath does not dispatch to Git common hooks: %s\n' "$configured" >&2
			return 69
		fi
	fi
	if [[ -e "$target" || -L "$target" ]]; then
		if ci_hooks_body | cmp -s - "$target"; then
			printf 'NO_OP hook already installed: %s\n' "$target"
			return 0
		fi
		printf 'Foreign pre-commit hook exists; preserve it: %s\n' "$target" >&2
		return 73
	fi
	printf 'PLAN install hook: %s\n' "$target"
}

# Summary: Install an owned pre-commit hook in the repository's common Git directory.
# Arguments: $1: repository root.
# Stdout: installation status.
# Stderr: actionable errors.
# Returns: 0 on installed or unchanged, nonzero on foreign hook or unsafe route.
# Side effects: creates one hook in the repository's common Git directory.
# Idempotency: an identical hook returns without replacement.
# Cleanup: removes its temporary file after success or handled failure.
ci_hooks_install() {
	local root="$1" common target temporary configured dispatcher plan
	plan="$(ci_hooks_plan "$root")" || return
	printf '%s\n' "$plan"
	if [[ "$plan" == NO_OP* ]]; then return 0; fi
	common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)" || return
	target="$common/hooks/pre-commit"
	configured="$(git -C "$root" config --get core.hooksPath || true)"
	if [[ -n "$configured" ]]; then
		if [[ "$configured" != /* ]]; then configured="$root/$configured"; fi
		dispatcher="$configured/pre-commit"
		# These are literal lines in the existing global Git hook dispatcher.
		# shellcheck disable=SC2016
		if [[ ! -x "$dispatcher" ]] ||
			! grep -Fq 'git_common_dir="$(git rev-parse --git-common-dir' "$dispatcher" ||
			! grep -Fq 'exec "$local_hook" "$@"' "$dispatcher"; then
			printf 'Configured hooksPath does not dispatch to Git common hooks: %s\n' "$configured" >&2
			return 69
		fi
	fi
	mkdir -p -- "$common/hooks" || return
	temporary="$(mktemp "$common/hooks/ci-skills-pre-commit.XXXXXX")" || return
	ci_hooks_body >"$temporary"
	chmod 0755 "$temporary" || {
		rm -f "$temporary"
		return 1
	}
	if [[ -e "$target" || -L "$target" ]]; then
		if cmp -s "$temporary" "$target"; then
			rm -f "$temporary"
			printf 'Hook already installed: %s\n' "$target"
			return 0
		fi
		rm -f "$temporary"
		printf 'Foreign pre-commit hook exists; preserve it: %s\n' "$target" >&2
		return 73
	fi
	ln "$temporary" "$target" 2>/dev/null || {
		rm -f "$temporary"
		printf 'Pre-commit hook appeared during installation; preserve it: %s\n' "$target" >&2
		return 73
	}
	rm -f "$temporary"
	[[ -x "$target" ]] || return 1
	printf 'Installed hook: %s\n' "$target"
}
