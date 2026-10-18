#!/bin/sh
# Stage exact stdin bytes without replacing caller files; remove only matching bytes.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -eu

pod_file_path=$1
pod_file_operation=$2

pod_file_remove() {
  if [ ! -e "$pod_file_path" ] && [ ! -L "$pod_file_path" ]; then
    printf 'absent\n'
    return
  fi
  read -r pod_file_expected_digest pod_file_expected_size
  pod_file_digest=$(sha256sum -- "$pod_file_path")
  pod_file_size=$(wc -c <"$pod_file_path")
  if [ "${pod_file_digest%% *}" != "$pod_file_expected_digest" ] ||
    [ "$pod_file_size" -ne "$pod_file_expected_size" ]; then
    printf 'owned file contents changed\n' >&2
    exit 32
  fi
  rm -- "$pod_file_path"
  [ ! -e "$pod_file_path" ] && [ ! -L "$pod_file_path" ]
  printf 'removed\n'
}

if [ "$pod_file_operation" = remove ]; then
  pod_file_remove
  exit
fi

pod_file_parent=${pod_file_path%/*}
umask 077
mkdir -p -- "$pod_file_parent"
pod_file_temporary=$(mktemp "$pod_file_parent/.ci-skills-input.XXXXXX")

pod_file_cleanup() {
  rm -f -- "$pod_file_temporary"
}

trap pod_file_cleanup EXIT
trap 'exit 130' HUP INT TERM
cat >"$pod_file_temporary"
if ln -T -- "$pod_file_temporary" "$pod_file_path" 2>/dev/null; then
  pod_file_ownership=created
elif [ "$pod_file_operation" = stage ] &&
  [ -f "$pod_file_path" ] && cmp -s -- "$pod_file_temporary" "$pod_file_path"; then
  pod_file_ownership=reused
else
  printf 'file creation failed or existing bytes differ\n' >&2
  exit 31
fi
pod_file_digest=$(sha256sum -- "$pod_file_path")
printf '%s\n' "${pod_file_digest%% *}"
wc -c <"$pod_file_path"
printf '%s\n' "$pod_file_ownership"
