#!/usr/bin/env bash
# Prove nested Podman execution inside the run-owned Kubernetes Job.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -euo pipefail

PROBE_DIRECTORY=$(cd -- "${BASH_SOURCE[0]%/*}" && pwd -P)
PROBE_RUNTIME_DIR=${PROBE_RUNTIME_DIR:-"$PROBE_DIRECTORY/../core"}
readonly PROBE_DIRECTORY PROBE_RUNTIME_DIR
# shellcheck source=ci-skills/lib/bash/core/bash_runtime.bash
source "$PROBE_RUNTIME_DIR/bash_runtime.bash"
if ((BASH_VERSINFO[0] < 5)); then
  PROBE_BASH5=$(ci_bash5_resolve) || exit "$CI_EXIT_BLOCKED"
  exec "$PROBE_BASH5" "$0" "$@"
fi

: "${INNER_IMAGE:?}"
: "${OK_NAME:?}"
: "${FAIL_NAME:?}"
: "${RUN_LABEL:?}"
: "${MARKER_PREFIX:?}"
: "${EVIDENCE_DIR:?}"
: "${EXPECTED_INNER_EXIT:?}"

# Summary: Invoke the fixed native-overlay runtime used by this probe.
# Arguments: Podman subcommand and its arguments.
# Stdout/Stderr: native command streams. Returns: native exit status.
podman_cmd() {
  podman --root /var/lib/containers/storage \
    --runroot /run/containers/storage \
    --storage-driver overlay \
    --storage-opt= \
    --cgroup-manager cgroupfs "$@"
}

# Summary: Remove this run's inner containers and report remaining identities.
# Stdout: remaining_containers marker. Stderr: cleanup diagnostics.
# Returns: zero only when removal succeeds and no run-labeled containers remain.
probe_cleanup() {
  local failed=0 remaining_output='' remaining=none
  ci_log info podman cleanup 'Removing the two run-owned inner containers.'
  if ! podman_cmd rm --force --ignore "$OK_NAME" "$FAIL_NAME"; then
    ci_log error podman cleanup 'Inner-container removal failed.'
    failed=1
  fi
  if remaining_output=$(podman_cmd ps -aq --filter "label=$RUN_LABEL"); then
    if [[ -n $remaining_output ]]; then
      remaining=${remaining_output//$'\n'/,}
      failed=1
    fi
  else
    ci_log error podman cleanup 'Remaining-container inventory failed.'
    failed=1
    remaining=inventory_failed
  fi
  printf '%s|remaining_containers|%s\n' "$MARKER_PREFIX" "$remaining"
  return "$failed"
}

# Summary: Preserve the probe failure, or fail a successful probe if cleanup fails.
# Arguments: original exit status. Returns: exits with final status.
probe_exit() {
  local status=$1
  trap - EXIT
  if ! probe_cleanup; then
    status=1
  fi
  exit "$status"
}

# Summary: Clean up, then re-raise the received signal with its native exit status.
# Arguments: signal name. Stderr: cleanup diagnostics.
# Returns: terminates this process by that signal.
probe_signal() {
  local signal=$1
  trap - EXIT "$signal"
  if ! probe_cleanup; then
    ci_log error podman signal 'Cleanup failed during interruption.'
  fi
  kill -s "$signal" "$$"
}

# Summary: Record host and native-overlay configuration before inner execution.
# Stdout: kernel, podman_version, storage and backing_filesystem markers.
# Returns: native command failure or zero.
probe_runtime() {
  local storage_info mount_program storage_driver graph_root cgroup_manager cgroup_version
  local kernel version backing_filesystem
  kernel=$(uname -sr)
  version=$(podman --version)
  printf '%s|kernel|%s\n' "$MARKER_PREFIX" "$kernel"
  printf '%s|podman_version|%s\n' "$MARKER_PREFIX" "$version"
  storage_info=$(podman_cmd info \
    --format '{{.Store.GraphDriverName}}|{{.Store.GraphRoot}}|{{.Host.CgroupManager}}|{{.Host.CgroupsVersion}}')
  IFS='|' read -r storage_driver graph_root cgroup_manager cgroup_version <<<"$storage_info"
  mount_program=$(podman_cmd info --format '{{index .Store.GraphOptions "overlay.mount_program"}}')
  if [[ -z $mount_program || $mount_program == '<no value>' ]]; then
    mount_program=none
  fi
  printf '%s|storage|driver=%s;mount_program=%s;cgroup_manager=%s;graph_root=%s;cgroup_version=%s\n' \
    "$MARKER_PREFIX" "$storage_driver" "$mount_program" "$cgroup_manager" \
    "$graph_root" "$cgroup_version"
  backing_filesystem=$(stat -f -c %T /var/lib/containers)
  printf '%s|backing_filesystem|%s\n' "$MARKER_PREFIX" "$backing_filesystem"
}

# Summary: Execute the checked inner script with a shared read-only script mount.
# Arguments: container name, then arguments to inner.sh.
# Stdout/Stderr: native inner-container streams. Returns: native exit status.
probe_inner() {
  local name=$1
  shift
  podman_cmd run --name "$name" --label "$RUN_LABEL" \
    --memory 64m --cpus 0.25 --security-opt label=disable \
    -v "$EVIDENCE_DIR:/evidence:rw" \
    -v "$PROBE_DIRECTORY:$PROBE_DIRECTORY:ro" \
    "$INNER_IMAGE" /bin/sh "$PROBE_DIRECTORY/inner.sh" "$@"
}

# Summary: Prove inner execution, bind read-back and native failure propagation.
# Stdout: inner_runtime, bind_readback and expected_exit markers.
# Returns: zero only when all actions and the expected failure are observed.
probe_execution() {
  local inner inner_id failure_rc bind_value
  inner=$(probe_inner "$OK_NAME" /evidence/inner.txt)
  inner_id=$(podman_cmd inspect --format '{{.Id}}' "$OK_NAME")
  printf '%s|inner_runtime|id=%s;%s\n' "$MARKER_PREFIX" "$inner_id" "$inner"
  bind_value=$(cat "$EVIDENCE_DIR/inner.txt")
  printf '%s|bind_readback|%s\n' "$MARKER_PREFIX" "$bind_value"
  if probe_inner "$FAIL_NAME" "$EXPECTED_INNER_EXIT"; then
    failure_rc=0
  else
    failure_rc=$?
    ci_log info podman expected_failure "Inner container returned status $failure_rc."
  fi
  printf '%s|expected_exit|%s\n' "$MARKER_PREFIX" "$failure_rc"
  [[ $failure_rc -eq $EXPECTED_INNER_EXIT ]]
}

trap 'probe_exit $?' EXIT
trap 'probe_signal HUP' HUP
trap 'probe_signal INT' INT
trap 'probe_signal TERM' TERM
probe_runtime
probe_execution
