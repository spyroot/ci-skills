#!/bin/sh
# Protect native asset paths and dispatch OpenShift node-image operations.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -eu

ocp_node_image_operation=$1
shift

ocp_node_image_preflight() {
  ocp_node_image_asset_directory=$1
  ocp_node_image_output_name=$2
  ocp_node_image_status_path=$3
  ocp_node_image_output_path="${ocp_node_image_asset_directory}/${ocp_node_image_output_name}"
  ocp_node_image_report_path="${ocp_node_image_asset_directory}/report.json"

  [ -d "$ocp_node_image_asset_directory" ] || exit 40
  if [ -e "$ocp_node_image_output_path" ] ||
    [ -L "$ocp_node_image_output_path" ]; then
    exit 41
  fi
  if [ -e "$ocp_node_image_report_path" ] ||
    [ -L "$ocp_node_image_report_path" ]; then
    exit 42
  fi
  if [ -e "$ocp_node_image_status_path" ] ||
    [ -L "$ocp_node_image_status_path" ]; then
    exit 45
  fi
  ocp_node_image_existing_iso=$(find \
    "$ocp_node_image_asset_directory" \
    -name '*.iso' \
    -print \
    -quit) || exit 44
  [ -z "$ocp_node_image_existing_iso" ] || exit 43
  printf '{"verified":true}\n'
}

ocp_node_image_run() {
  ocp_node_image_asset_directory=$1
  ocp_node_image_output_name=$2
  ocp_node_image_kubeconfig=$3
  ocp_node_image_registry_config=$4
  ocp_node_image_status_path=$5
  shift 5

  REGISTRY_AUTH_FILE=$ocp_node_image_registry_config
  export REGISTRY_AUTH_FILE
  if oc \
    --kubeconfig "$ocp_node_image_kubeconfig" \
    adm node-image create \
    --dir "$ocp_node_image_asset_directory" \
    --output-name "$ocp_node_image_output_name" \
    "$@"; then
    ocp_node_image_exit_code=0
  else
    ocp_node_image_exit_code=$?
  fi

  ocp_node_image_status_temp="${ocp_node_image_status_path}.tmp.$$"
  umask 077
  (
    set -C
    printf '{"exit_code":%s}\n' "$ocp_node_image_exit_code" > \
      "$ocp_node_image_status_temp"
  ) || exit 74
  if ! ln "$ocp_node_image_status_temp" "$ocp_node_image_status_path"; then
    rm -f "$ocp_node_image_status_temp"
    exit 75
  fi
  rm -f "$ocp_node_image_status_temp"
  exit "$ocp_node_image_exit_code"
}

ocp_node_image_status() {
  ocp_node_image_status_path=$1
  if [ ! -f "$ocp_node_image_status_path" ] ||
    [ -L "$ocp_node_image_status_path" ]; then
    exit 46
  fi
  cat "$ocp_node_image_status_path"
}

ocp_node_image_cleanup_failed() {
  ocp_node_image_asset_directory=$1
  ocp_node_image_output_name=$2
  ocp_node_image_output_path="${ocp_node_image_asset_directory}/${ocp_node_image_output_name}"
  ocp_node_image_report_path="${ocp_node_image_asset_directory}/report.json"

  [ -d "$ocp_node_image_asset_directory" ] || exit 40
  for ocp_node_image_failed_path in \
    "$ocp_node_image_output_path" \
    "$ocp_node_image_report_path"; do
    if [ -L "$ocp_node_image_failed_path" ] ||
      { [ -e "$ocp_node_image_failed_path" ] &&
        [ ! -f "$ocp_node_image_failed_path" ]; }; then
      exit 47
    fi
    if [ -f "$ocp_node_image_failed_path" ]; then
      rm -f -- "$ocp_node_image_failed_path"
    fi
    if [ -e "$ocp_node_image_failed_path" ] ||
      [ -L "$ocp_node_image_failed_path" ]; then
      exit 48
    fi
  done
  printf '{"verified":true}\n'
}

ocp_node_image_inspect() {
  ocp_node_image_path=$1
  exec coreos-installer iso ignition show "$ocp_node_image_path"
}

case "$ocp_node_image_operation" in
preflight)
  ocp_node_image_preflight "$@"
  ;;
run)
  ocp_node_image_run "$@"
  ;;
status)
  ocp_node_image_status "$@"
  ;;
cleanup-failed)
  ocp_node_image_cleanup_failed "$@"
  ;;
inspect)
  ocp_node_image_inspect "$@"
  ;;
*)
  exit 64
  ;;
esac
