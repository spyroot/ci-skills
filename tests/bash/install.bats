#!/usr/bin/env bats

setup() {
  repo_root="$(cd -- "${BATS_TEST_DIRNAME}/../.." && pwd -P)"
  destination="${BATS_TEST_TMPDIR}/skills/ci-skills"
  fake_bin="${BATS_TEST_TMPDIR}/bin"
  mkdir -p "$fake_bin"
  cat >"$fake_bin/glab" <<'GLAB'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == skills && $3 == glab-stack ]] || exit 64
case $2 in
  get) printf '# glab-stack fixture\n' ;;
  install)
    [[ $4 == --path ]] || exit 64
    mkdir -p "$5/glab-stack"
    printf '# glab-stack fixture\n' >"$5/glab-stack/SKILL.md"
    ;;
  *) exit 64 ;;
esac
GLAB
  chmod +x "$fake_bin/glab"
  PATH="$fake_bin:$PATH"
  export PATH
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
  caller_root="${BATS_TEST_TMPDIR}/consumer"
  mkdir -p "$caller_root"
  git -C "$caller_root" init --quiet
  cd "$caller_root" || return 1
}

# Summary: Verify a user-scoped dry-run plans the expected path under HOME without writing.
# Arguments:
#   $1: --scope value.
#   $2: Expected relative path under HOME.
# Returns: 0 when destination matches and HOME remains absent.
assert_user_scope_dry_run() {
  local scope=$1 expected_rel=$2 home
  make_install_fixture
  home="${BATS_TEST_TMPDIR}/home"
  run env HOME="$home" "$tool" --scope "$scope" --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .destination <<<"$output")" = "$(cd "$BATS_TEST_TMPDIR" && pwd -P)/home/${expected_rel}" ]
  [ ! -e "$home" ]
}

# Summary: Verify a project-scoped dry-run resolves the Git root from a subdirectory without writing.
# Arguments:
#   $1: --scope value.
#   $2: Top-level project directory name (for example .claude or .agents).
# Returns: 0 when destination matches and the project directory remains absent.
assert_project_scope_subdir_dry_run() {
  local scope=$1 project_dir=$2
  make_install_fixture
  mkdir -p nested/dir
  cd nested/dir || return 1
  run "$tool" --scope "$scope" --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .destination <<<"$output")" = "$(cd "$caller_root" && pwd -P)/${project_dir}/skills/ci-skills" ]
  [ ! -e "${caller_root}/${project_dir}" ]
}

# Summary: Verify a project scope outside a Git repository emits BLOCKED JSON without usage text.
# Arguments:
#   $1: --scope value.
#   $2: Top-level project directory name that must remain absent.
# Returns: 0 when exit status, JSON payload, and clean filesystem match.
assert_project_scope_outside_repo_blocked() {
  local scope=$1 project_dir=$2 outside
  make_install_fixture
  outside="${BATS_TEST_TMPDIR}/outside"
  mkdir -p "$outside"
  cd "$outside" || return 1
  run env GIT_CEILING_DIRECTORIES="$BATS_TEST_TMPDIR" "$tool" --scope "$scope" --dry-run
  [ "$status" -eq 2 ]
  jq -e --arg scope "$scope" \
    '.status == "BLOCKED" and .kind == "skill_install" and .scope == $scope
    and .reason == "agent_repository_unavailable" and (.safe_next_step | length > 0)' \
    <<<"$output" >/dev/null
  [[ "$output" != *'usage:'* ]]
  [ ! -e "${outside}/${project_dir}" ]
}

@test 'dry-run binds a clean package and destination without writing' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .mode <<<"$output")" = "dry-run" ]
  [ "$(jq -r .source <<<"$output")" = "$source_root/ci-skills" ]
  [ "$(jq -r .destination <<<"$output")" = "$destination" ]
  [ "$(jq -r .source_revision <<<"$output")" = "$source_revision" ]
  [ "$(jq -r .agent.destination <<<"$output")" = "$caller_root/.agents/skills" ]
  jq -e '.mutable_link == false and (.fingerprint | length == 64)' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}

@test 'scope codex-home reads back the same destination as no location flag' {
  make_install_fixture
  home="${BATS_TEST_TMPDIR}/home"
  run env -u CODEX_HOME HOME="$home" "$tool" --dry-run
  [ "$status" -eq 0 ]
  default_destination=$(jq -r .destination <<<"$output")
  run env -u CODEX_HOME HOME="$home" "$tool" --scope codex-home --dry-run
  [ "$status" -eq 0 ]
  [ "$(jq -r .destination <<<"$output")" = "$default_destination" ]
  [ "$default_destination" = "$(cd "$BATS_TEST_TMPDIR" && pwd -P)/home/.codex/skills/ci-skills" ]
  [ ! -e "$home" ]
}

@test 'scope claude-user plans the Claude user skills directory without writing' {
  assert_user_scope_dry_run claude-user .claude/skills/ci-skills
}

@test 'scope claude-project plans the consumer Git root from a subdirectory without writing' {
  assert_project_scope_subdir_dry_run claude-project .claude
}

@test 'scope claude-project outside a Git repository reports BLOCKED JSON without usage text' {
  assert_project_scope_outside_repo_blocked claude-project .claude
}

@test 'scope antigravity-user plans the Antigravity user skills directory without writing' {
  assert_user_scope_dry_run antigravity-user .gemini/config/skills/ci-skills
}

@test 'scope antigravity-project plans the consumer Git root from a subdirectory without writing' {
  assert_project_scope_subdir_dry_run antigravity-project .agents
}

@test 'scope antigravity-project outside a Git repository reports BLOCKED JSON without usage text' {
  assert_project_scope_outside_repo_blocked antigravity-project .agents
}

@test 'scope antigravity-project apply installs ci-skills alongside glab-stack and upgrades idempotently' {
  make_install_fixture
  run "$tool" --scope antigravity-project --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --scope antigravity-project --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 0 ]
  [ "$(jq -r .status <<<"$output")" = "PASS" ]
  [ -d "$caller_root/.agents/skills/ci-skills" ]
  [ ! -L "$caller_root/.agents/skills/ci-skills" ]
  [ -f "$caller_root/.agents/skills/glab-stack/SKILL.md" ]
  cmp "$source_root/ci-skills/SKILL.md" "$caller_root/.agents/skills/ci-skills/SKILL.md"
  run "$tool" --scope antigravity-project --upgrade --dry-run
  [ "$status" -eq 0 ]
  upgrade_fingerprint=$(jq -r .fingerprint <<<"$output")
  run "$tool" --scope antigravity-project --upgrade --apply \
    --timeout 10s --confirm-upgrade "$upgrade_fingerprint"
  [ "$status" -eq 0 ]
  jq -e '.status == "PASS" and .already_installed == true' <<<"$output" >/dev/null
}

@test 'scope cannot be combined with another location flag' {
  make_install_fixture
  run "$tool" --scope claude-user --destination "$destination" --dry-run
  [ "$status" -eq 2 ]
  [[ "$output" == *'not allowed with argument'* ]]
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
  [ -f "$caller_root/.agents/skills/glab-stack/SKILL.md" ]
  [ ! -e "$source_root/.agents/skills/glab-stack" ]
  cmp "$source_root/ci-skills/SKILL.md" "$destination/SKILL.md"
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
  [ "$(dirname "$backup")" = "$(dirname "$destination")/.ci-skills.backups" ]
  [ "$(readlink "$backup")" = "$source_root" ]
  cmp "$source_root/ci-skills/SKILL.md" "$destination/SKILL.md"
}

@test 'antigravity-user upgrade stores backups outside top-level skill discovery' {
  make_install_fixture
  home="${BATS_TEST_TMPDIR}/home"
  skills_dir="$(cd "$BATS_TEST_TMPDIR" && pwd -P)/home/.gemini/config/skills"
  run env HOME="$home" "$tool" --scope antigravity-user --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  run env HOME="$home" "$tool" --scope antigravity-user --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 0 ]
  printf '\n# Upgraded skill\n' >>"${source_root}/ci-skills/SKILL.md"
  git -C "$source_root" add ci-skills/SKILL.md
  git -C "$source_root" commit --quiet -m "upgrade skill"
  run env HOME="$home" "$tool" --scope antigravity-user --upgrade --dry-run
  [ "$status" -eq 0 ]
  upgrade_fingerprint=$(jq -r .fingerprint <<<"$output")
  run env HOME="$home" "$tool" --scope antigravity-user --upgrade --apply \
    --timeout 10s --confirm-upgrade "$upgrade_fingerprint"
  [ "$status" -eq 0 ]
  backup=$(jq -r .previous_version <<<"$output")
  [ -d "$backup" ]
  [ "$(dirname "$backup")" = "${skills_dir}/.ci-skills.backups" ]
  [ -f "${backup}/SKILL.md" ]
  mapfile -t manifests < <(find "$skills_dir" -mindepth 2 -maxdepth 2 -name SKILL.md | sort)
  [ "${#manifests[@]}" -eq 1 ]
  [ "${manifests[0]}" = "${skills_dir}/ci-skills/SKILL.md" ]
}

@test 'apply blocks when tracked source bytes changed after planning' {
  make_install_fixture
  run "$tool" --destination "$destination" --dry-run
  [ "$status" -eq 0 ]
  fingerprint=$(jq -r .fingerprint <<<"$output")
  printf '\nchanged after dry-run\n' >>"${source_root}/ci-skills/SKILL.md"
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
  printf '\nnew committed skill\n' >>"${source_root}/ci-skills/SKILL.md"
  git -C "$source_root" add ci-skills/SKILL.md
  git -C "$source_root" commit --quiet -m "change skill"
  run "$tool" --destination "$destination" --apply \
    --timeout 10s --confirm-install "$fingerprint"
  [ "$status" -eq 2 ]
  jq -e '.status == "BLOCKED" and .reason == "confirmation_required"' <<<"$output" >/dev/null
  [ ! -e "$destination" ]
}
