#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=ci-skills/lib/bash/toolchain/conda.bash
source "${BASH_SOURCE[0]%/*}/../../ci-skills/lib/bash/toolchain/conda.bash"
CI_toolchain_main conda "$@"
