#!/usr/bin/env bash
# Register the selected runner in its writable shared runtime directory.
# Inputs are explicit Pod environment fields; credentials are Secret references.
set -euo pipefail

: "${RUNNER_SOURCE_ROOT:?}"
: "${RUNNER_RUNTIME_ROOT:?}"
: "${RUNNER_HOST:?}"
: "${RUNNER_REPOSITORY:?}"
: "${RUNNER_NAME:?}"
: "${RUNNER_LABELS:?}"

cp -R -- "${RUNNER_SOURCE_ROOT}/." "${RUNNER_RUNTIME_ROOT}/"
cd -- "${RUNNER_RUNTIME_ROOT}"
registration_token=$(gh api --hostname "${RUNNER_HOST}" \
  --method POST "repos/${RUNNER_REPOSITORY}/actions/runners/registration-token" \
  --jq .token)
[[ -n "${registration_token}" && "${registration_token}" != null ]]
./config.sh --unattended --replace --disableupdate \
  --url "https://${RUNNER_HOST}/${RUNNER_REPOSITORY}" \
  --token "${registration_token}" --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS}" --work _work
unset registration_token GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN
