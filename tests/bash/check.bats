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
  run ci_check_run
  PATH=$old_path
  [ "$status" -eq 69 ]
  [[ "$output" == *'source revision changed during validation'* ]]
  [[ "$output" == *'Rerun this gate against one stable exact source commit.'* ]]
}
