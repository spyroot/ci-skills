#!/usr/bin/env bash
set -Eeuo pipefail

# Example-only YAML emitter. It does not contact OpenShift or build an image.
# Historical Standards input: name standards-runner-5d6e746; namespace
# standards-ci; source SHA 5d6e7462881a36bc2665235ce66da4479d90a45d;
# image harbor.dcloud.run/standards/ci-toolbox:candidate-5d6e7462881a;
# pull and push Secret name harbor-dcloud-run.

usage() {
  cat <<'HELP'
Usage: emit-binary-buildconfig.sh --name NAME --namespace NAMESPACE
  --source-sha SHA40 --dockerfile RELATIVE_PATH --image IMAGE_TAG
  --pull-secret NAME --push-secret NAME --source-label-key KEY [--dry-run]

Print a Binary BuildConfig YAML document to stdout. No cluster mutation.
All values are caller-selected. --help prints this message.
HELP
}

fail() {
  printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$1" 'See --help and supply valid explicit values.' >&2
  exit 64
}

name='' namespace='' source_sha='' dockerfile='' image=''
pull_secret='' push_secret='' source_label_key=''
while (($#)); do
  case $1 in
  --help)
    usage
    exit 0
    ;;
  --dry-run) shift ;;
  --name | --namespace | --source-sha | --dockerfile | --image | --pull-secret | --push-secret | --source-label-key)
    (($# >= 2)) || fail "missing value for $1"
    case $1 in
    --name) name=$2 ;;
    --namespace) namespace=$2 ;;
    --source-sha) source_sha=$2 ;;
    --dockerfile) dockerfile=$2 ;;
    --image) image=$2 ;;
    --pull-secret) pull_secret=$2 ;;
    --push-secret) push_secret=$2 ;;
    --source-label-key) source_label_key=$2 ;;
    esac
    shift 2
    ;;
  *) fail "unknown argument: $1" ;;
  esac
done

dns_name='^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
[[ $name =~ $dns_name && $namespace =~ $dns_name ]] || fail 'invalid resource name or namespace'
[[ $pull_secret =~ $dns_name && $push_secret =~ $dns_name ]] || fail 'invalid Secret name'
[[ $source_sha =~ ^[0-9a-f]{40}$ ]] || fail 'source SHA must be a full 40-character commit'
[[ $dockerfile =~ ^[A-Za-z0-9._/-]+$ && $dockerfile != /* && $dockerfile != ../* && $dockerfile != */../* ]] ||
  fail 'dockerfile must be a relative repository path'
[[ $image =~ ^[A-Za-z0-9][A-Za-z0-9._/@:+-]*$ ]] || fail 'invalid image reference'
[[ $source_label_key =~ ^[A-Za-z0-9./_-]+$ ]] || fail 'invalid source label key'

root=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
awk -v name="$name" -v namespace="$namespace" -v source_sha="$source_sha" \
  -v dockerfile="$dockerfile" -v image="$image" -v pull_secret="$pull_secret" \
  -v push_secret="$push_secret" -v source_label_key="$source_label_key" '
  {
    gsub(/@NAME@/, name)
    gsub(/@NAMESPACE@/, namespace)
    gsub(/@SOURCE_SHA@/, source_sha)
    gsub(/@DOCKERFILE@/, dockerfile)
    gsub(/@IMAGE@/, image)
    gsub(/@PULL_SECRET@/, pull_secret)
    gsub(/@PUSH_SECRET@/, push_secret)
    gsub(/@SOURCE_LABEL_KEY@/, source_label_key)
    print
  }
' "$root/examples/binary-buildconfig.yaml.in"
