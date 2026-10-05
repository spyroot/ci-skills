#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=lib/bash/toolchain/conda.bash
source "${BASH_SOURCE[0]%/*}/../../lib/bash/toolchain/conda.bash"
CI_toolchain_main conda "$@"
