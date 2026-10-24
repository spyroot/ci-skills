#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../../bin/ci-api"
  export CI_TEST_GH_MARKER="${BATS_TEST_TMPDIR}/gh-calls"
  mkdir -p "${BATS_TEST_TMPDIR}/mock"
  export CI_TEST_API_AUTH="${BATS_TEST_TMPDIR}/native-identity.bash"
  export CI_TEST_GH_AUTH_MARKER="${BATS_TEST_TMPDIR}/github-identity"
  export CI_TEST_API_CALLS="${BATS_TEST_TMPDIR}/api-calls"
  export CI_TEST_GH_USER=unit-admin
  export CI_TEST_GH_ADMIN=true CI_TEST_GL_ADMIN=true
  export GH_TOKEN=fixture-token GH_ENTERPRISE_TOKEN=fixture-enterprise-token
  export GITLAB_TOKEN=fixture-gitlab-token
  export CI_SKILLS_TARGET="${BATS_TEST_TMPDIR}/target.toml"
  cat > "$CI_SKILLS_TARGET" <<'TARGET'
[github]
host = "github.com"
repository = "example/repo"
[gitlab]
url = "https://gitlab.example.invalid"
TARGET
  cat > "$CI_TEST_API_AUTH" <<'IDENTITY'
ci_test_native_identity() {
  local tool="$1" endpoint="" host=github.com previous="" argument
  shift
  printf '%s\n' "$tool $*" >> "$CI_TEST_API_CALLS"
  for argument in "$@"; do
    if [[ $previous == --hostname ]]; then
      host=$argument
    fi
    case "$argument" in
      user | repos/*) endpoint=$argument ;;
    esac
    previous=$argument
  done
  if [[ $endpoint == user ]]; then
    if [[ $tool == gh ]]; then
      printf '{"id":17,"login":"%s"}\n' "$CI_TEST_GH_USER"
    else
      printf '{"id":17,"username":"unit-admin","is_admin":%s,"web_url":"https://%s/unit-admin"}\n' \
        "$CI_TEST_GL_ADMIN" "$host"
    fi
    return 0
  fi
  if [[ $tool == gh && $endpoint == repos/* && ! -e $CI_TEST_GH_AUTH_MARKER ]]; then
    local selected="${endpoint#repos/}" owner repository
    owner="${selected%%/*}"
    selected="${selected#*/}"
    repository="${selected%%/*}"
    repository="${repository%%\?*}"
    printf '%s\n' verified > "$CI_TEST_GH_AUTH_MARKER"
    printf '{"id":42,"full_name":"%s/%s","permissions":{"admin":%s}}\n' \
      "$owner" "$repository" "$CI_TEST_GH_ADMIN"
    return 0
  fi
  return 1
}
ci_test_native_identity "$@"
IDENTITY
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' 'provider was called' >&2
exit 99
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
  [ ! -e "$CI_TEST_API_CALLS" ]
}

@test 'GitHub repository admin refusal prevents the requested endpoint read' {
  export CI_TEST_GH_ADMIN=false
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' requested > "$CI_TEST_GH_MARKER"
printf '%s\n' '{"content":"unreachable"}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github \
    --endpoint repos/example/repo/contents/file --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *github_required_role_missing* ]]
  [ ! -e "$CI_TEST_GH_MARKER" ]
}

@test 'GitLab instance admin refusal prevents the requested endpoint read' {
  export CI_TEST_GL_ADMIN=false
  cat > "${BATS_TEST_TMPDIR}/mock/glab" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' requested > "$CI_TEST_GH_MARKER"
printf '%s\n' '{"id":42}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/glab"
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs/42 --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *gitlab_required_role_missing* ]]
  [ ! -e "$CI_TEST_GH_MARKER" ]
}

@test 'unknown field fails instead of printing a null result' {
  run "$tool" get --provider github --endpoint repos/example/repo \
    --field absent --log-level error
  [ "$status" -eq 65 ]
}

@test 'false is a present JSON field' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' '{"enabled":false}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --field enabled --output json --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = false ]
}

@test 'HTTP 429 GET is retried and then returns data' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
  export CI_TEST_SLEEP_MARKER="${BATS_TEST_TMPDIR}/sleep-called"
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' '[{"id":1}]' '[{"id":2}]'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/glab"
  run "$tool" get --provider gitlab --host gitlab.example.invalid \
    --endpoint projects/7/jobs --paginate --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":1},{"id":2}]' ]
}

@test 'GitHub paginated JSON arrays combine into one collection' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' '[[{"id":1}],[{"id":2}]]'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo/issues \
    --paginate --log-level error
  [ "$status" -eq 0 ]
  [ "$output" = '[{"id":1},{"id":2}]' ]
}

@test 'GitHub pagination rejects a non-collection page' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' '[[{"id":1}],{"id":2}]'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo/issues \
    --paginate --log-level error
  [ "$status" -eq 65 ]
  [[ "$output" == *'invalid paginated JSON'* ]]
}

@test 'Enterprise GitHub token file overrides every ambient token source' {
  export CI_TEST_GH_USER=selected
  cat > "$CI_SKILLS_TARGET" <<'TARGET'
[github]
host = "github.enterprise.example.test"
repository = "example/repo"
TARGET
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
[[ ${GH_ENTERPRISE_TOKEN:-} == ci-selected-token ]] || exit 97
[[ ! ${GH_TOKEN+x} ]] || exit 98
[[ ! ${GITHUB_TOKEN+x} && ! ${GITHUB_ENTERPRISE_TOKEN+x} ]] || exit 99
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' '{"login":"selected"}'
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  printf '%s\n' ci-selected-token > "${BATS_TEST_TMPDIR}/github-enterprise-token"
  export GH_TOKEN=wrong-cloud-token GH_ENTERPRISE_TOKEN=wrong-enterprise-token
  export GITHUB_TOKEN=wrong-alias-token
  export GITHUB_ENTERPRISE_TOKEN=wrong-enterprise-alias-token
  run "$tool" check --provider github --host github.enterprise.example.test \
    --token-file "${BATS_TEST_TMPDIR}/github-enterprise-token" --log-level error
  [ "$status" -eq 0 ]
  [ "$(jq -r '.provider' <<< "$output")" = github ]
  [ "$(jq -r '.host' <<< "$output")" = github.enterprise.example.test ]
  [ "$(jq -r '.user' <<< "$output")" = selected ]
  [[ "$output" != *ci-selected-token* ]]
}

@test 'connection refusal is classified and never displays a token' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
printf '%s\n' called > "$CI_TEST_GH_MARKER"
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/gh"
  run "$tool" get --provider github --endpoint repos/example/repo \
    --token-file "${BATS_TEST_TMPDIR}/missing-token" --log-level error
  [ "$status" -eq 66 ]
  [ ! -e "$CI_TEST_GH_MARKER" ]
}

@test 'HTTP 404 fails without retry and names the missing resource class' {
  cat > "${BATS_TEST_TMPDIR}/mock/gh" <<'MOCK'
#!/usr/bin/env bash
bash "$CI_TEST_API_AUTH" "${0##*/}" "$@" && exit 0
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
