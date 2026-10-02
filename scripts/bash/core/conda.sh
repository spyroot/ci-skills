#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=lib/bash/toolchain/conda.bash
source "${BASH_SOURCE[0]%/*}/../../lib/bash/toolchain/conda.bash"
galileo_toolchain_main conda "$@"
