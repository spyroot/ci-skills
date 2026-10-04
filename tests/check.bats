#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../scripts/check.sh"
  source "${BATS_TEST_DIRNAME}/../lib/ci/check.bash"
}

make_clean_git_fixture() {
  fixture="${BATS_TEST_TMPDIR}/source"
  mkdir -p "$fixture"
  git -C "$fixture" init --quiet
  git -C "$fixture" config user.email ci-skills-tests@example.invalid
  git -C "$fixture" config user.name "ci-skills tests"
  printf '%s\n' '# Fixture skill' >"${fixture}/SKILL.md"
  git -C "$fixture" add SKILL.md
  git -C "$fixture" commit --quiet -m 'fixture source'
}

make_check_run_fixture() {
  make_clean_git_fixture
  mkdir -p "${fixture}/skills/ci-skills"
  printf '%s\n' '# Fixture skill' >"${fixture}/skills/ci-skills/SKILL.md"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"${fixture}/check.sh"
  printf '%s\n' 'name: fixture' >"${fixture}/fixture.yaml"
  git -C "$fixture" add check.sh fixture.yaml skills/ci-skills/SKILL.md
  git -C "$fixture" commit --quiet -m 'add gate inputs'
}

make_success_stubs() {
  stubs="${BATS_TEST_TMPDIR}/stubs"
  mkdir -p "$stubs"
  for command in shellcheck shfmt yamllint markdownlint-cli2 gitleaks; do
    cat >"${stubs}/${command}" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "${stubs}/${command}"
  done
}

@test 'check dry-run lists the planned gate without running tests' {
  run "$tool" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
  [[ "$output" == *'"unit"'* ]]
}

@test 'check dry-run selects exactly one named gate' {
  run "$tool" --dry-run --gate yaml
  [ "$status" -eq 0 ]
  [[ "$output" == *'"checks":["yaml"]'* ]]
  [[ "$output" != *'"unit"'* ]]
}

@test 'check dry-run expands static gate to non-unit checks' {
  run "$tool" --dry-run --gate static
  [ "$status" -eq 0 ]
  [[ "$output" == *'"checks":["whitespace","bash-n","shellcheck","shfmt","yaml","markdown","secrets","neutrality","manifest"]'* ]]
  [[ "$output" != *'"unit"'* ]]
}

@test 'every registered gate has an explicit static or CI class' {
  for gate in "${CI_CHECK_GATES[@]}"; do
    case ${CI_CHECK_CLASS_BY_GATE[$gate]:-} in
    static | ci) ;;
    *) false ;;
    esac
  done
  [ "${CI_CHECK_CLASS_BY_GATE[unit]}" = ci ]
  [ "${CI_CHECK_CLASS_BY_GATE[neutrality]}" = static ]
  [ "${CI_CHECK_CLASS_BY_GATE[manifest]}" = static ]
}

@test 'check rejects an unknown gate before execution' {
  run "$tool" --dry-run --gate missing
  [ "$status" -eq 64 ]
  [[ "$output" == *'unknown gate: missing'* ]]
}

@test 'check help names every accepted gate and the all default' {
  run "$tool" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *'all (default)'* ]]
  [[ "$output" == *'static'* ]]
  for gate in "${CI_CHECK_GATES[@]}"; do
    [[ "$output" == *"$gate"* ]]
  done
}

@test 'each named gate dispatches only its own steps' {
  make_check_run_fixture
  CI_CHECK_ROOT="$fixture"
  CI_CHECK_CALLS="${BATS_TEST_TMPDIR}/calls"
  export CI_CHECK_CALLS
  ci_check_step() {
    printf '%s\n' "$1" >>"$CI_CHECK_CALLS"
  }
  for gate in "${CI_CHECK_GATES[@]}"; do
    : >"$CI_CHECK_CALLS"
    run ci_check_run "$gate"
    [ "$status" -eq 0 ]
    [[ "$output" == *"\"checks\":[\"$gate\"]"* ]]
    expected=$gate
    if [ "$gate" = bash-n ]; then
      expected='bash-n:check.sh'
    fi
    [ "$(cat "$CI_CHECK_CALLS")" = "$expected" ]
  done
  : >"$CI_CHECK_CALLS"
  run ci_check_run all
  [ "$status" -eq 0 ]
  [ "$(cat "$CI_CHECK_CALLS")" = "$(printf '%s\n' \
    whitespace bash-n:check.sh shellcheck shfmt yaml markdown secrets \
    neutrality manifest unit)" ]
}

@test 'static gate dispatches all non-unit steps and reports them' {
  make_check_run_fixture
  CI_CHECK_ROOT="$fixture"
  CI_CHECK_CALLS="${BATS_TEST_TMPDIR}/calls"
  export CI_CHECK_CALLS
  ci_check_step() {
    printf '%s\n' "$1" >>"$CI_CHECK_CALLS"
  }
  run ci_check_run static
  [ "$status" -eq 0 ]
  [[ "$output" == *'"checks":["whitespace","bash-n","shellcheck","shfmt","yaml","markdown","secrets","neutrality","manifest"]'* ]]
  [ "$(cat "$CI_CHECK_CALLS")" = "$(printf '%s\n' \
    whitespace whitespace-staged whitespace-unstaged bash-n:check.sh \
    shellcheck shfmt yaml markdown secrets neutrality manifest)" ]
}

@test 'named yaml gate does not require unrelated gate tools' {
  ci_check_tool_present() {
    case $1 in
    bash | git | jq | yamllint) return 0 ;;
    *) return 1 ;;
    esac
  }
  run ci_check_tools yaml
  [ "$status" -eq 0 ]
}

@test 'named yaml gate requires yamllint' {
  ci_check_tool_present() {
    [ "$1" != yamllint ]
  }
  run ci_check_tools yaml
  [ "$status" -eq 69 ]
  [[ "$output" == *'required check tool is missing: yamllint'* ]]
}

@test 'static gate requires only non-unit gate tools' {
  CI_CHECK_CALLS="${BATS_TEST_TMPDIR}/tool-calls"
  export CI_CHECK_CALLS
  ci_check_tool_present() {
    printf '%s\n' "$1" >>"$CI_CHECK_CALLS"
    case $1 in
    bash | git | jq | shellcheck | shfmt | yamllint | markdownlint-cli2 | gitleaks | python)
      return 0
      ;;
    *)
      return 1
      ;;
    esac
  }
  run ci_check_tools static
  [ "$status" -eq 0 ]
  for tool in bash git jq shellcheck shfmt yamllint markdownlint-cli2 gitleaks python; do
    grep -qx "$tool" "$CI_CHECK_CALLS"
  done
  for tool in bats yq gh glab; do
    ! grep -qx "$tool" "$CI_CHECK_CALLS"
  done
}

@test 'named yaml gate runs only its selected command' {
  make_check_run_fixture
  make_success_stubs
  for command in shellcheck shfmt yamllint markdownlint-cli2 gitleaks bats; do
    cat >"${stubs}/${command}" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "${0##*/}" >>"$CI_CHECK_CALLS"
exit 0
STUB
    chmod +x "${stubs}/${command}"
  done
  CI_CHECK_CALLS="${BATS_TEST_TMPDIR}/calls"
  export CI_CHECK_CALLS
  CI_CHECK_ROOT="$fixture"
  KUBERNETES_SERVICE_HOST=fixture
  export KUBERNETES_SERVICE_HOST
  old_path=$PATH
  PATH="${stubs}:$PATH"
  run ci_check_main --gate yaml
  PATH=$old_path
  [ "$status" -eq 0 ]
  [[ "$output" == *'"checks":["yaml"]'* ]]
  [ "$(cat "$CI_CHECK_CALLS")" = yamllint ]
}

@test 'named gate failure blocks the selected gate' {
  make_check_run_fixture
  make_success_stubs
  cat >"${stubs}/yamllint" <<'STUB'
#!/usr/bin/env bash
exit 7
STUB
  chmod +x "${stubs}/yamllint"
  CI_CHECK_ROOT="$fixture"
  KUBERNETES_SERVICE_HOST=fixture
  export KUBERNETES_SERVICE_HOST
  old_path=$PATH
  PATH="${stubs}:$PATH"
  run ci_check_main --gate yaml
  PATH=$old_path
  [ "$status" -eq 69 ]
  [[ "$output" == *'yaml failed (exit 7)'* ]]
}

@test 'static gate reports the first failing selected gate' {
  make_check_run_fixture
  make_success_stubs
  cat >"${stubs}/shellcheck" <<'STUB'
#!/usr/bin/env bash
exit 7
STUB
  chmod +x "${stubs}/shellcheck"
  CI_CHECK_ROOT="$fixture"
  KUBERNETES_SERVICE_HOST=fixture
  export KUBERNETES_SERVICE_HOST
  old_path=$PATH
  PATH="${stubs}:$PATH"
  run ci_check_main --gate static
  PATH=$old_path
  [ "$status" -eq 69 ]
  [[ "$output" == *'shellcheck failed (exit 7)'* ]]
}

@test 'static gate runs locally on a dirty worktree without claiming a commit' {
  make_check_run_fixture
  make_success_stubs
  cat >"${stubs}/python" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "python:$1" >>"$CI_CHECK_CALLS"
exit 0
STUB
  chmod +x "${stubs}/python"
  CI_CHECK_CALLS="${BATS_TEST_TMPDIR}/calls"
  export CI_CHECK_CALLS
  printf '%s\n' '# pending edit' >>"${fixture}/check.sh"
  CI_CHECK_ROOT="$fixture"
  old_path=$PATH
  PATH="${stubs}:$PATH"
  run env -u KUBERNETES_SERVICE_HOST bash -c 'source "$1"; CI_CHECK_ROOT="$2"; ci_check_main --gate static' \
    _ "${BATS_TEST_DIRNAME}/../lib/ci/check.bash" "$fixture"
  PATH=$old_path
  [ "$status" -eq 0 ]
  [[ "$output" == *'"source":"working-tree","commit":null'* ]]
  [ "$(grep -c '^python:' "$CI_CHECK_CALLS")" -eq 2 ]
}

@test 'check refuses execution outside Kubernetes' {
  run env -u KUBERNETES_SERVICE_HOST "$tool" --log-level error
  [ "$status" -eq 69 ]
  [[ "$output" == *'Kubernetes execution is required'* ]]
}

@test 'check rejects log files inside the repository before dry-run output' {
  inside="${BATS_TEST_DIRNAME}/../check.log"
  run "$tool" --dry-run --log-file "$inside"
  [ "$status" -eq 69 ]
  [[ "$output" == *'log file must be outside the source checkout'* ]]
  [[ "$output" == *'Choose a --log-file path outside this repository.'* ]]
}

@test 'check allows log files outside the repository' {
  outside_dir="${BATS_TEST_TMPDIR}/logs"
  mkdir -p "$outside_dir"
  run "$tool" --dry-run --log-file "${outside_dir}/check.log"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
}

@test 'check rejects outside log-file symlink into the repository' {
  outside_dir="${BATS_TEST_TMPDIR}/logs"
  mkdir -p "$outside_dir"
  ln -s "${BATS_TEST_DIRNAME}/../check.log" "${outside_dir}/check.log"
  run "$tool" --dry-run --log-file "${outside_dir}/check.log"
  [ "$status" -eq 69 ]
  [[ "$output" == *'log file must not be a symlink'* ]]
  [[ "$output" == *'Choose a regular --log-file path outside this repository.'* ]]
}

@test 'verified source revision accepts a clean exact source tree' {
  make_clean_git_fixture
  revision="$(git -C "$fixture" rev-parse HEAD)"
  run ci_verified_source_revision "$fixture"
  [ "$status" -eq 0 ]
  [ "$output" = "$revision" ]
}

@test 'verified source revision rejects tracked working tree changes' {
  make_clean_git_fixture
  printf '%s\n' 'tracked dirty' >>"${fixture}/SKILL.md"
  run ci_verified_source_revision "$fixture"
  [ "$status" -eq 69 ]
  [[ "$output" == *'skill source has uncommitted changes'* ]]
  [[ "$output" == *'Commit or remove source changes'* ]]
}

@test 'verified source revision rejects staged index changes' {
  make_clean_git_fixture
  printf '%s\n' 'index dirty' >>"${fixture}/SKILL.md"
  git -C "$fixture" add SKILL.md
  run ci_verified_source_revision "$fixture"
  [ "$status" -eq 69 ]
  [[ "$output" == *'skill source has uncommitted changes'* ]]
  [[ "$output" == *'Commit or remove source changes'* ]]
}

@test 'verified source revision rejects untracked source files' {
  make_clean_git_fixture
  printf '%s\n' 'untracked dirty' >"${fixture}/extra.txt"
  run ci_verified_source_revision "$fixture"
  [ "$status" -eq 69 ]
  [[ "$output" == *'skill source has uncommitted changes'* ]]
  [[ "$output" == *'Commit or remove source changes'* ]]
}

@test 'check rejects a clean head swap before exact revision evidence' {
  make_check_run_fixture
  make_success_stubs
  cat >"${stubs}/bats" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' 'changed during checks' >> SKILL.md
git add SKILL.md
git commit --quiet -m 'swap clean head during check'
exit 0
STUB
  chmod +x "${stubs}/bats"
  CI_CHECK_ROOT="$fixture"
  old_path=$PATH
  PATH="${stubs}:$PATH"
  run ci_check_run unit
  PATH=$old_path
  [ "$status" -eq 69 ]
  [[ "$output" == *'source revision changed during validation'* ]]
  [[ "$output" == *'Rerun this gate against one stable exact source commit.'* ]]
}
