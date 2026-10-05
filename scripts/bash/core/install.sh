#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=lib/bash/toolchain/install.bash
source "${BASH_SOURCE[0]%/*}/../../lib/bash/toolchain/install.bash"
CI_toolchain_main tools "$@"
