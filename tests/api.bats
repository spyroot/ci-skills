#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../bin/ci-api"
  mkdir -p "${BATS_TEST_TMPDIR}/mock"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' '{"content":"b25lCnR3bwp0aHJlZQo="}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  export PATH="${BATS_TEST_TMPDIR}/mock:${PATH}"
}

@test 'GitHub file content is decoded and bounded to selected source lines' {
  run "$tool" get --provider github \
    --endpoint 'repos/example/repo/contents/src/file?ref=v1' \
    --field content --decode-base64 --lines 2:2 --output text \
    --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = '2:two' ]
}

@test 'dry-run prints a plan and makes no provider call' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' 'provider was called' >&2
exit 99
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
}

@test 'unknown field fails instead of printing a null result' {
  run "$tool" get --provider github --endpoint repos/example/repo \
    --field absent --log-level error
  [ "$status" -eq 65 ]
}

@test 'HTTP 429 GET is retried and then returns data' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-called"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
if [[ ! -e $CI_TEST_GH_MARKER ]]; then
  printf '%s\n' called > "$CI_TEST_GH_MARKER"
  printf '%s\n' 'HTTP 429: rate limited' >&2
  exit 1
fi
printf '%s\n' '{"value":"ready"}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --field value --output text --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = ready ]
  [ -e "$CI_TEST_GH_MARKER" ]
}

@test 'HTTP 401 GET fails without retry' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-calls"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called >> "$CI_TEST_GH_MARKER"
printf '%s\n' 'HTTP 401: unauthorized' >&2
exit 1
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --log-level error
  [ "$status" -eq 69 ]
  [ "$(wc -l < "$CI_TEST_GH_MARKER")" -eq 1 ]
}
