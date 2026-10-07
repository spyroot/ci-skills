#!/usr/bin/env bash
set -Eeuo pipefail

# shellcheck source=ci-skills/lib/bash/toolchain/install.bash
source "${BASH_SOURCE[0]%/*}/../../ci-skills/lib/bash/toolchain/install.bash"
CI_toolchain_main tools "$@"
