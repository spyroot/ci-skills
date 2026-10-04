#!/usr/bin/env bats

setup() {
  repo_root="$(cd -- "${BATS_TEST_DIRNAME}/.." && pwd -P)"
  destination="${BATS_TEST_TMPDIR}/skills/ci-skills"
}

make_install_fixture() {
  local path target_dir
  source_root="${BATS_TEST_TMPDIR}/source"
  mkdir -p "$source_root"
  while IFS= read -r -d '' path; do
    [[ -e "${repo_root}/${path}" || -L "${repo_root}/${path}" ]] || continue
    target_dir="${source_root}/$(dirname -- "$path")"
    mkdir -p "$target_dir"
    cp -Pp "${repo_root}/${path}" "${source_root}/${path}"
  done < <(git -C "$repo_root" ls-files -z)
  git -C "$source_root" init --quiet
  git -C "$source_root" config user.email ci-skills-tests@example.invalid
  git -C "$source_root" config user.name "ci-skills tests"
  git -C "$source_root" add -A
  git -C "$source_root" commit --quiet -m "fixture source"
  source_revision="$(git -C "$source_root" rev-parse HEAD)"
  tool="${source_root}/install.sh"
}

@test 'dry-run binds a clean package and destination without writing' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .mode <<<"$output")" = "dry-run" ]
  [ "$(jq -r .source <<<"$output")" = "$source_root/skills/ci-skills" ]
  [ "$(jq -r .destination <<<"$output")" = "$destination" ]
  [ "$(jq -r .source_revision <<<"$output")" = "$source_revision" ]
  jq -e '.mutable_link == false and (.fingerprint | length == 64)' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}

@test 'requested diagnostics record a correlated structured installer result' {
  make_install_fixture
  log_file="${BATS_TEST_TMPDIR}/installer.log"
  run "$tool" --destination "$destination" --dry-run \
    --log-format json --log-level info --log-file "$log_file" --run-id unit-install
  [ "$status" -eq 0 ]
  jq -e '.component == "skill_install" and .run_id == "unit-install" and .result == "DRY_RUN"' \
    "$log_file" >/dev/null
  [[ "$output" == *'"fingerprint"'* ]]
}

@test 'apply copies the selected package and upgrade reads back an identical copy' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 0 ]
  [ -d "$destination" ]
  [ ! -L "$destination" ]
  [ "$(jq -r .status <<<"$output")" = "PASS" ]
  cmp "$source_root/skills/ci-skills/SKILL.md" "$destination/SKILL.md"
  run "$tool" --destination "$destination" --upgrade --dry-run
  [ "$status" -eq 0 ]
  upgrade_fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --upgrade --apply \
    --timeout 10s --confirm-upgrade "$upgrade_fingerprint"
  [ "$status" -eq 0 ]
  jq -e '.status == "PASS" and .already_installed == true' <<<"$output" >/dev/null
}

@test 'install preserves an existing destination' {
  make_install_fixture
  mkdir -p "$destination"
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 2 ]
  jq -e '.status == "BLOCKED" and .reason == "destination_exists"' <<<"$output" >/dev/null
  [ -d "$destination" ]
}

@test 'confirmed upgrade replaces a checkout link and preserves it as backup' {
  make_install_fixture
  mkdir -p "$(dirname "$destination")"
  ln -s "$source_root" "$destination"
  run "$tool" --destination "$destination" --upgrade --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --upgrade --apply \
    --timeout 10s --confirm-upgrade "$fingerprint"
  [ "$status" -eq 0 ]
  [ -d "$destination" ]
  [ ! -L "$destination" ]
  backup=$(jq -r .previous_version <<<"$output")
  [ -L "$backup" ]
  [ "$(readlink "$backup")" = "$source_root" ]
  cmp "$source_root/skills/ci-skills/SKILL.md" "$destination/SKILL.md"
}

@test 'apply blocks when tracked source bytes changed after planning' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  printf '\nchanged after dry-run\n' >>"${source_root}/skills/ci-skills/SKILL.md"
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 2 ]
  jq -e '.status == "BLOCKED" and .reason == "source_revision_unverified"' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}

@test 'apply blocks when source revision changed after planning' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  printf '\nnew committed skill\n' >>"${source_root}/skills/ci-skills/SKILL.md"
  git -C "$source_root" add skills/ci-skills/SKILL.md
  git -C "$source_root" commit --quiet -m "change skill"
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 2 ]
  jq -e '.status == "BLOCKED" and .reason == "confirmation_required"' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}
