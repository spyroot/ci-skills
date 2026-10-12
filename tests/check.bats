#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../scripts/check.sh"
}

@test 'check dry-run lists the planned gate without running tests' {
  run "$tool" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
  [[ "$output" == *'"unit"'* ]]
}

@test 'check refuses execution outside Kubernetes' {
  run env -u KUBERNETES_SERVICE_HOST "$tool" --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *'Kubernetes execution is required'* ]]
}
