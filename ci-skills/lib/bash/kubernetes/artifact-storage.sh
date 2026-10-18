#!/bin/sh
# Perform exact-path storage operations inside a prepared Kubernetes tool Pod.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -eu

artifact_storage_operation=$1
shift

artifact_storage_observe_mount() {
  artifact_storage_path=$1
  [ -d "$artifact_storage_path" ] || exit 40
  exec findmnt \
    --json \
    --target "$artifact_storage_path" \
    --output TARGET,FSTYPE,SOURCE
}

artifact_storage_ensure_directory() {
  artifact_storage_path=$1
  if [ -d "$artifact_storage_path" ]; then
    printf '{"created":false}\n'
    return
  fi
  if [ -e "$artifact_storage_path" ] || [ -L "$artifact_storage_path" ]; then
    exit 41
  fi
  mkdir -- "$artifact_storage_path" || exit 48
  printf '{"created":true}\n'
}

artifact_storage_probe() {
  artifact_storage_path=$1
  artifact_storage_probe_path=$2
  artifact_storage_probe_value=$3

  if [ -e "$artifact_storage_path" ] || [ -L "$artifact_storage_path" ]; then
    exit 42
  fi
  umask 077
  set -C
  if ! printf '%s' "$artifact_storage_probe_value" \
    >"$artifact_storage_probe_path"; then
    exit 43
  fi
  artifact_storage_cleanup_probe() {
    rm -f -- "$artifact_storage_probe_path"
  }
  trap artifact_storage_cleanup_probe EXIT
  trap 'exit 130' HUP INT TERM
  [ "$(cat "$artifact_storage_probe_path")" = \
    "$artifact_storage_probe_value" ] || exit 44
  rm -f -- "$artifact_storage_probe_path"
  if [ -e "$artifact_storage_probe_path" ] ||
    [ -L "$artifact_storage_probe_path" ]; then
    exit 45
  fi
  trap - EXIT HUP INT TERM
  printf '{"verified":true}\n'
}

artifact_storage_copy() {
  artifact_storage_source=$1
  artifact_storage_destination=$2

  [ -f "$artifact_storage_source" ] || exit 50
  if [ -e "$artifact_storage_destination" ] ||
    [ -L "$artifact_storage_destination" ]; then
    exit 51
  fi
  umask 077
  set -C
  if ! : >"$artifact_storage_destination"; then
    exit 52
  fi
  artifact_storage_cleanup_destination() {
    rm -f -- "$artifact_storage_destination"
  }
  trap artifact_storage_cleanup_destination EXIT
  trap 'exit 130' HUP INT TERM
  if ! cat "$artifact_storage_source" >>"$artifact_storage_destination"; then
    exit 53
  fi
  artifact_storage_emit_identity "$artifact_storage_destination"
  trap - EXIT HUP INT TERM
}

artifact_storage_emit_identity() {
  artifact_storage_path=$1
  [ -f "$artifact_storage_path" ] || exit 60
  artifact_storage_digest=$(sha256sum -- "$artifact_storage_path") || exit 61
  artifact_storage_digest=${artifact_storage_digest%% *}
  artifact_storage_size=$(wc -c <"$artifact_storage_path") || exit 62
  artifact_storage_size=$(printf '%s' "$artifact_storage_size" | tr -d '[:space:]')
  printf '{"sha256":"%s","size":%s}\n' \
    "$artifact_storage_digest" "$artifact_storage_size"
}

artifact_storage_remove_file() {
  artifact_storage_path=$1
  if [ -e "$artifact_storage_path" ] || [ -L "$artifact_storage_path" ]; then
    rm -f -- "$artifact_storage_path" || exit 70
  fi
  if [ -e "$artifact_storage_path" ] || [ -L "$artifact_storage_path" ]; then
    exit 71
  fi
  printf '{"absent":true}\n'
}

artifact_storage_remove_directory() {
  artifact_storage_path=$1
  if [ -d "$artifact_storage_path" ]; then
    rmdir -- "$artifact_storage_path" || exit 72
  fi
  if [ -e "$artifact_storage_path" ] || [ -L "$artifact_storage_path" ]; then
    exit 73
  fi
  printf '{"absent":true}\n'
}

case "$artifact_storage_operation" in
mount-info)
  artifact_storage_observe_mount "$@"
  ;;
ensure-directory)
  artifact_storage_ensure_directory "$@"
  ;;
probe)
  artifact_storage_probe "$@"
  ;;
copy)
  artifact_storage_copy "$@"
  ;;
verify)
  artifact_storage_emit_identity "$@"
  ;;
remove-file)
  artifact_storage_remove_file "$@"
  ;;
remove-directory)
  artifact_storage_remove_directory "$@"
  ;;
*)
  exit 64
  ;;
esac
