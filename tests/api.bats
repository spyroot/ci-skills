#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../bin/ci-api"
  mkdir -p "${BATS_TEST_TMPDIR}/mock"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' '{"content":"b25lCnR3\nbwp0aHJlZQo="}'
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

@test 'false is a present JSON field' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' '{"enabled":false}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --field enabled --output json --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = false ]
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

@test 'Retry-After beyond the budget stops without sleeping or retrying' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-calls"
  export CI_TEST_SLEEP_MARKER="${BATS_TEST_TMPDIR}/sleep-called"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called >> "$CI_TEST_GH_MARKER"
printf '%s\n' 'HTTP 429: rate limited' 'Retry-After: 6' >&2
exit 1
MOCK
  cat > "${BATS_TEST_TMPDIR}/mock/sleep" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called > "$CI_TEST_SLEEP_MARKER"
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh" "${BATS_TEST_TMPDIR}/mock/sleep"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *'Retry-After exceeds'* ]]
  [ "$(wc -l < "$CI_TEST_GH_MARKER")" -eq 1 ]
  [ ! -e "$CI_TEST_SLEEP_MARKER" ]
}

@test 'GitLab plain-text trace can return a bounded line range' {
  cat > "${BATS_TEST_TMPDIR}/mock/glab" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' 'first trace line' 'second trace line' 'third trace line'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/glab"
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs/42/trace --output text --lines 2:2 \
    --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = '2:second trace line' ]
}

@test 'GitLab JSON field selection works with an explicit token file' {
  cat > "${BATS_TEST_TMPDIR}/mock/glab" <<'MOCK'
#!/usr/bin/env bash
[[ ${GITLAB_TOKEN:-} == ci-test-token ]] || exit 97
printf '%s\n' '{"status":"success"}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/glab"
  printf '%s\n' ci-test-token > "${BATS_TEST_TMPDIR}/gitlab-token"
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs/42 --token-file "${BATS_TEST_TMPDIR}/gitlab-token" \
    --field status --output text --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = success ]
}

@test 'GitLab paginated JSON arrays combine into one collection' {
  cat > "${BATS_TEST_TMPDIR}/mock/glab" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' '[{"id":1}]' '[{"id":2}]'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/glab"
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs --paginate --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":1},{"id":2}]' ]
}

@test 'connection refusal is classified and never displays a token' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-calls"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called >> "$CI_TEST_GH_MARKER"
printf '%s\n' 'error connecting: connection refused' >&2
exit 1
MOCK
  cat > "${BATS_TEST_TMPDIR}/mock/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh" "${BATS_TEST_TMPDIR}/mock/sleep"
  printf '%s\n' ci-test-token > "${BATS_TEST_TMPDIR}/github-token"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --token-file "${BATS_TEST_TMPDIR}/github-token" --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *CONNECTIVITY_FAILED* ]]
  [[ "$output" != *ci-test-token* ]]
  [ "$(wc -l < "$CI_TEST_GH_MARKER")" -eq 3 ]
}

@test 'multiline token file fails before the provider is called' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-called"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called > "$CI_TEST_GH_MARKER"
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  printf '%s\n' ci-test-token second-line > "${BATS_TEST_TMPDIR}/github-token"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --token-file "${BATS_TEST_TMPDIR}/github-token" --log-level error
  [ "$status" -eq 65 ]
  [[ "$output" != *ci-test-token* ]]
  [ ! -e "$CI_TEST_GH_MARKER" ]
}

@test 'unreadable token path fails before the provider is called' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-called"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called > "$CI_TEST_GH_MARKER"
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --token-file "${BATS_TEST_TMPDIR}/missing-token" --log-level error
  [ "$status" -eq 66 ]
  [ ! -e "$CI_TEST_GH_MARKER" ]
}

@test 'HTTP 404 fails without retry and names the missing resource class' {
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-calls"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' called >> "$CI_TEST_GH_MARKER"
printf '%s\n' 'HTTP 404: Not Found' >&2
exit 1
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/missing \
    --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *RESOURCE_NOT_FOUND* ]]
  [ "$(wc -l < "$CI_TEST_GH_MARKER")" -eq 1 ]
}

@test 'raw response rejects pagination' {
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs/42/trace --output text --paginate
  [ "$status" -eq 64 ]
}
