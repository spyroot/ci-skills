#!/usr/bin/env bash
set -Eeuo pipefail

CI_INSTALL_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
output_option=--json
for argument in "$@"; do
  if [[ $argument == --json || $argument == --yaml ]]; then
    output_option=
    break
  fi
done

# The Python installer owns planning, confirmation, copy, and read-back.
if [[ -n $output_option ]]; then
  exec "${CI_SKILLS_PYTHON:-python3}" \
    "$CI_INSTALL_ROOT/tools/install_ci_skills.py" "$output_option" "$@"
fi
exec "${CI_SKILLS_PYTHON:-python3}" \
  "$CI_INSTALL_ROOT/tools/install_ci_skills.py" "$@"
