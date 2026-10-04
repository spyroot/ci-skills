#!/usr/bin/env bash
set -Eeuo pipefail

CI_SKILLS_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
# shellcheck source=lib/ci/check.bash
source "$CI_SKILLS_ROOT/lib/ci/check.bash"
ci_check_main "$@"
