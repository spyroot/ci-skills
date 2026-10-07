#!/usr/bin/env bash

[[ ${CI_SKILLS_BINARY_BUILD_LOADED:-0} == 1 ]] && return 0
CI_SKILLS_BINARY_BUILD_LOADED=1
CI_BINARY_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
# shellcheck source=ci-skills/lib/bash/core/runtime.bash
source "$CI_BINARY_ROOT/core/runtime.bash"

ci_binary_build_help() {
  cat <<'HELP'
ci-binary-build: inspect one OpenShift Binary BuildConfig plan

Usage:
  ci-binary-build --spec FILE --source-repo DIR --source-commit SHA \
    --commit-label KEY [--context NAME] [--dry-run]

Options:
  --spec FILE           Caller-owned BuildConfig YAML or JSON.
  --source-repo DIR     Local Git repository containing the source commit.
  --source-commit SHA   Exact 40-character commit to plan.
  --commit-label KEY    BuildConfig metadata label holding that commit.
  --context NAME        Optional target context recorded in the plan.
  --dry-run             Print validated plan; no cluster call (default).
  --log-format text|json  Diagnostic format (default: text).
  --log-level debug|info|warning|error  Minimum diagnostic level.
  --log-file PATH       Append diagnostics to PATH when requested.
  --run-id ID           Caller-supplied diagnostic correlation ID.
  --help                Show this help without credentials.

Input must be a Binary/Docker BuildConfig that outputs a DockerImage. The
Dockerfile must exist at the selected Git commit. The plan fingerprints the
manifest and exact Git tree. Apply is not implemented until ownership,
cleanup, and independent registry read-back have a verified route.

Exit: 0 valid plan, 64 usage, 65 invalid data, 66 missing input,
69 blocked. The command never contacts OpenShift.
HELP
}

# Summary: Extract required BuildConfig fields without changing the manifest.
# Arguments: canonical JSON manifest, commit-label key, source commit.
# Stdout: JSON with build identity and Dockerfile path.
# Returns: 0 or invalid-data class.
ci_binary_manifest_fields() {
  local manifest=$1 label=$2 commit=$3
  jq -ce --arg label "$label" --arg commit "$commit" '
    select(.apiVersion == "build.openshift.io/v1" and .kind == "BuildConfig")
    | select(.spec.source.type == "Binary" and .spec.strategy.type == "Docker")
    | select(.spec.output.to.kind == "DockerImage")
    | select(.metadata.labels[$label] == $commit)
    | {
        name: .metadata.name,
        namespace: .metadata.namespace,
        contextDir: (.spec.source.contextDir // ""),
        dockerfilePath: .spec.strategy.dockerStrategy.dockerfilePath,
        image: .spec.output.to.name,
        pullSecret: (.spec.strategy.dockerStrategy.pullSecret.name // null),
        pushSecret: (.spec.output.pushSecret.name // null)
      }
    | select((.name|type)=="string" and (.name|length)>0)
    | select((.namespace|type)=="string" and (.namespace|length)>0)
    | select((.contextDir|type)=="string")
    | select((.dockerfilePath|type)=="string" and (.dockerfilePath|length)>0)
    | select((.image|type)=="string" and (.image|length)>0)
  ' <<<"$manifest"
}

# Summary: Validate a caller manifest against one exact Git tree.
# Arguments: command-line arguments.
# Stdout: JSON plan with a SHA-256 fingerprint. Stderr: diagnostics.
# Returns: 0, usage, invalid-data, missing, or blocked class.
ci_binary_build_main() {
  local spec='' source_repo='' commit='' label='' context='' manifest=''
  local fields='' repo_root='' tree='' plan='' fingerprint='' help=false
  CI_LOG_FORMAT=text CI_LOG_LEVEL=info CI_LOG_FILE='' CI_RUN_ID=''
  if (($# == 0)); then
    ci_binary_build_help >&2
    return "$CI_EXIT_USAGE"
  fi
  while (($#)); do
    case $1 in
    --spec | --source-repo | --source-commit | --commit-label | --context | --log-format | --log-level | --log-file | --run-id)
      (($# >= 2)) || ci_fail "$CI_EXIT_USAGE" "$1 needs a value" 'See --help.' || return $?
      case $1 in
      --spec) spec=$2 ;; --source-repo) source_repo=$2 ;;
      --source-commit) commit=$2 ;; --commit-label) label=$2 ;;
      --context) context=$2 ;; --log-format) CI_LOG_FORMAT=$2 ;;
      --log-level) CI_LOG_LEVEL=$2 ;; --log-file) CI_LOG_FILE=$2 ;;
      --run-id) CI_RUN_ID=$2 ;;
      esac
      shift 2
      ;;
    --dry-run) shift ;;
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
    ci_binary_build_help
    return 0
  }
  [[ -n $spec && -n $source_repo && -n $commit && -n $label ]] ||
    ci_fail "$CI_EXIT_USAGE" 'spec, source repo, source commit, and commit label are required' \
      'Supply all required arguments shown by --help.' || return $?
  [[ $commit =~ ^[0-9a-f]{40}$ ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_FORMAT == text || $CI_LOG_FORMAT == json ]] || return "$CI_EXIT_USAGE"
  [[ $CI_LOG_LEVEL =~ ^(debug|info|warning|error)$ ]] || return "$CI_EXIT_USAGE"
  [[ -r $spec && -d $source_repo ]] || return "$CI_EXIT_MISSING"
  command -v yq >/dev/null 2>&1 || ci_fail "$CI_EXIT_BLOCKED" 'yq is missing' 'Install yq.' || return $?
  command -v jq >/dev/null 2>&1 || ci_fail "$CI_EXIT_BLOCKED" 'jq is missing' 'Install jq.' || return $?
  command -v git >/dev/null 2>&1 || ci_fail "$CI_EXIT_BLOCKED" 'git is missing' 'Install git.' || return $?
  manifest=$(yq -o=json '.' "$spec" |
    jq -sc 'if length == 1 then .[0] else error("expected one BuildConfig") end') ||
    return "$CI_EXIT_DATA"
  fields=$(ci_binary_manifest_fields "$manifest" "$label" "$commit") || ci_fail \
    "$CI_EXIT_DATA" 'BuildConfig fields or commit label are invalid' \
    'Check the Binary/Docker/DockerImage manifest and selected label.' || return $?
  repo_root=$(git -C "$source_repo" rev-parse --show-toplevel) || return "$CI_EXIT_DATA"
  git -C "$repo_root" cat-file -e "$commit^{commit}" || return "$CI_EXIT_DATA"
  local dockerfile context_dir source_dockerfile
  dockerfile=$(jq -r .dockerfilePath <<<"$fields")
  context_dir=$(jq -r .contextDir <<<"$fields")
  source_dockerfile=$dockerfile
  if [[ -n $context_dir && $context_dir != . ]]; then
    source_dockerfile="${context_dir%/}/$dockerfile"
  fi
  git -C "$repo_root" cat-file -e "$commit:$source_dockerfile" || ci_fail \
    "$CI_EXIT_DATA" 'Dockerfile is absent at the selected commit' \
    'Choose a commit containing the manifest Dockerfile path.' || return $?
  tree=$(git -C "$repo_root" rev-parse "$commit^{tree}") || return "$CI_EXIT_DATA"
  plan=$(jq -cnS --argjson manifest "$manifest" --arg sourceRepo "$repo_root" \
    --arg sourceCommit "$commit" --arg sourceTree "$tree" \
    --arg context "$context" --arg sourceDockerfilePath "$source_dockerfile" \
    '{manifest:$manifest,sourceRepo:$sourceRepo,sourceCommit:$sourceCommit,sourceTree:$sourceTree,context:$context,sourceDockerfilePath:$sourceDockerfilePath}') || return
  fingerprint=$(printf '%s' "$plan" | ci_sha256_stdin) || return
  ci_log info ci-binary-build plan 'validated one Binary BuildConfig' || return
  jq -cn --arg fingerprint "$fingerprint" --argjson plan "$plan" \
    '{mode:"dry-run",fingerprint:$fingerprint,plan:$plan}'
}
