#!/bin/sh
# Run the minimal bind/cgroup proof, or return the caller's expected failure.
# Argument: an absolute evidence filename, or a numeric exit status.
# Stdout: inner-runtime evidence. Returns: native failure or selected status.
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com
set -eu

case "$1" in
/*)
  printf ready >"$1"
  printf 'marker=inner-ok;cgroup='
  tr '\n' ',' </proc/self/cgroup
  printf ';root='
  grep -m 1 ' / ' /proc/self/mountinfo
  ;;
*) exit "$1" ;;
esac
