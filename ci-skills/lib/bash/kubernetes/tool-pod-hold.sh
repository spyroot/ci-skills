#!/bin/sh
# Keep a prepared tool Pod available for bounded native exec operations.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -eu

tool_pod_stop() {
  exit 0
}

trap tool_pod_stop HUP INT TERM
while :; do
  sleep 3600 &
  wait "$!"
done
