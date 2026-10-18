#!/usr/bin/env bash
# Register one pre-created GitLab runner for the toolbox-backed shell executor.
# Inputs are explicit Pod environment fields; the token is an owned Secret key.
set -euo pipefail

: "${RUNNER_RUNTIME_ROOT:?}"
: "${RUNNER_URL:?}"
: "${RUNNER_NAME:?}"
: "${RUNNER_TOKEN:?}"

command -v gitlab-runner >/dev/null
command -v podman >/dev/null

readonly STORAGE_CONF="${RUNNER_RUNTIME_ROOT}/storage.conf"
readonly CONTAINERS_CONF="${RUNNER_RUNTIME_ROOT}/containers.conf"
readonly GRAPH_ROOT="/var/lib/containers/storage"
readonly RUN_ROOT="/run/containers/storage"

install -d -m 0700 -- \
  "${RUNNER_RUNTIME_ROOT}" \
  "${GRAPH_ROOT}" \
  "${RUN_ROOT}"
cat >"${STORAGE_CONF}" <<EOF
[storage]
driver = "overlay"
graphroot = "${GRAPH_ROOT}"
runroot = "${RUN_ROOT}"
EOF
cat >"${CONTAINERS_CONF}" <<'EOF'
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
EOF

gitlab-runner register --non-interactive \
  --url "${RUNNER_URL}" \
  --token "${RUNNER_TOKEN}" \
  --executor shell \
  --name "${RUNNER_NAME}" \
  --builds-dir "${RUNNER_RUNTIME_ROOT}/builds" \
  --cache-dir "${RUNNER_RUNTIME_ROOT}/cache" \
  --env "CONTAINERS_STORAGE_CONF=${STORAGE_CONF}" \
  --env "CONTAINERS_CONF=${CONTAINERS_CONF}" \
  --config "${RUNNER_RUNTIME_ROOT}/config.toml"
unset RUNNER_TOKEN
