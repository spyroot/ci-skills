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

@test 'dry-run reports a fingerprint without creating the destination' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .mode <<<"$output")" = "dry-run" ]
  [ "$(jq -r .source <<<"$output")" = "$source_root" ]
  [ "$(jq -r .destination <<<"$output")" = "$destination" ]
  [ "$(jq -r .source_revision <<<"$output")" = "$source_revision" ]
  jq -e '.mutable_link == true' <<<"$output" >/dev/null
  jq -e '.fingerprint | type == "string" and length > 0' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}

@test 'apply creates the requested link and repetition is a no-op' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 0 ]
  [ -L "$destination" ]
  [ "$(readlink "$destination")" = "$source_root" ]
  [ "$(jq -r .status <<<"$output")" = "READY" ]
  [ "$(jq -r .destination <<<"$output")" = "$destination" ]
  [ "$(jq -r .source <<<"$output")" = "$source_root" ]
  [ "$(jq -r .source_revision <<<"$output")" = "$source_revision" ]
  jq -e '.mutable_link == true' <<<"$output" >/dev/null
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 0 ]
  [ "$(jq -r .status <<<"$output")" = "NO_OP" ]
  [ "$(jq -r .destination <<<"$output")" = "$destination" ]
  [ "$(jq -r .source <<<"$output")" = "$source_root" ]
  [ "$(jq -r .source_revision <<<"$output")" = "$source_revision" ]
  jq -e '.mutable_link == true' <<<"$output" >/dev/null
}

@test 'apply preserves a destination owned by something else' {
  make_install_fixture
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

@test 'apply blocks when tracked source bytes changed after dry-run' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  printf '\nchanged after dry-run\n' >>"${source_root}/README.md"
  [ "$(git -C "$source_root" rev-parse HEAD)" = "$source_revision" ]
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 69 ]
  [ ! -e "$destination" ]
}

@test 'apply blocks when source revision changed after dry-run' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  printf '\nchanged and committed after dry-run\n' >>"${source_root}/README.md"
  git -C "$source_root" add README.md
  git -C "$source_root" commit --quiet -m "change source after dry-run"
  [ "$(git -C "$source_root" rev-parse HEAD)" != "$source_revision" ]
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint" --log-level error
  [ "$status" -eq 69 ]
  [ ! -e "$destination" ]
}
