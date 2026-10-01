#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../install.sh"
  destination="${BATS_TEST_TMPDIR}/skills/ci-skills"
}

@test 'dry-run reports a fingerprint without creating the destination' {
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
  [ ! -e "$destination" ]
}

@test 'apply creates the requested link and repetition is a no-op' {
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 0 ]
  [ -L "$destination" ]
  [[ "$output" == *'"status":"READY"'* ]]
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status":"NO_OP"'* ]]
}

@test 'apply preserves a destination owned by something else' {
  mkdir -p "$destination"
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 69 ]
  [ -d "$destination" ]
  [ ! -L "$destination" ]
}
