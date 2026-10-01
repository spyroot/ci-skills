#!/usr/bin/env bash
set -Eeuo pipefail

# Example-only YAML emitter for the no-output vcpkg prerequisite probe.
# The former scratch YAML was removed after the build. Historical inputs:
# base harbor.dcloud.run/standards/ci-toolbox@sha256:2f24237280db9b380ffa39e7979b4704e204dfb323ee8b73c2b155dbb599283a;
# vcpkg ref 4a1c77189c64dae7afd478333a64d1e604d5dc91;
# packages zip=3.0-13+deb12u1 and unzip=6.0-28+deb12u1.

usage() {
  cat <<'HELP'
Usage: emit-vcpkg-prereq.sh --name NAME --namespace NAMESPACE
  --base-image IMAGE_AT_SHA256 --vcpkg-ref SHA40
  --zip-package PACKAGE=VERSION --unzip-package PACKAGE=VERSION
  --pull-secret NAME [--dry-run]

Print a no-output vcpkg prerequisite BuildConfig YAML document to stdout.
It does not contact OpenShift, run vcpkg, or push an image.
HELP
}

fail() {
  printf 'BLOCKER: %s\nSAFE_NEXT_STEP: %s\n' "$1" 'See --help and supply valid explicit values.' >&2
  exit 64
}

name='' namespace='' base_image='' vcpkg_ref=''
zip_package='' unzip_package='' pull_secret=''
while (($#)); do
  case $1 in
    --help) usage; exit 0 ;;
    --dry-run) shift ;;
    --name|--namespace|--base-image|--vcpkg-ref|--zip-package|--unzip-package|--pull-secret)
      (($# >= 2)) || fail "missing value for $1"
      case $1 in
        --name) name=$2 ;;
        --namespace) namespace=$2 ;;
        --base-image) base_image=$2 ;;
        --vcpkg-ref) vcpkg_ref=$2 ;;
        --zip-package) zip_package=$2 ;;
        --unzip-package) unzip_package=$2 ;;
        --pull-secret) pull_secret=$2 ;;
      esac
      shift 2 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

dns_name='^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
[[ $name =~ $dns_name && $namespace =~ $dns_name && $pull_secret =~ $dns_name ]] ||
  fail 'invalid resource, namespace, or Secret name'
[[ $base_image =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*@sha256:[0-9a-f]{64}$ ]] ||
  fail 'base image must use an immutable sha256 digest'
[[ $vcpkg_ref =~ ^[0-9a-f]{40}$ ]] || fail 'vcpkg ref must be a full 40-character commit'
[[ $zip_package =~ ^zip=[A-Za-z0-9.+:~_-]+$ ]] || fail 'invalid pinned zip package'
[[ $unzip_package =~ ^unzip=[A-Za-z0-9.+:~_-]+$ ]] || fail 'invalid pinned unzip package'

root=$(cd -- "$(dirname -- "$0")/.." && pwd -P)
awk -v name="$name" -v namespace="$namespace" -v base_image="$base_image" \
  -v vcpkg_ref="$vcpkg_ref" -v zip_package="$zip_package" \
  -v unzip_package="$unzip_package" -v pull_secret="$pull_secret" '
  {
    gsub(/@NAME@/, name)
    gsub(/@NAMESPACE@/, namespace)
    gsub(/@BASE_IMAGE@/, base_image)
    gsub(/@VCPKG_REF@/, vcpkg_ref)
    gsub(/@ZIP_PACKAGE@/, zip_package)
    gsub(/@UNZIP_PACKAGE@/, unzip_package)
    gsub(/@PULL_SECRET@/, pull_secret)
    print
  }
' "$root/examples/vcpkg-prereq.yaml.in"
