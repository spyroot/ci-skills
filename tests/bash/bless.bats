#!/usr/bin/env bats
# bless.sh and lib/bash/automation/bless.bash: the pre-commit hook.
# The fixture commit exercises the local linters used by make bless, including jq.

setup() {
  export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:$PATH
  repo_root="$(cd -- "${BATS_TEST_DIRNAME}/../.." && pwd -P)"
  # shellcheck source=lib/bash/automation/bless.bash
  source "${repo_root}/lib/bash/automation/bless.bash"
  fixture="${BATS_TEST_TMPDIR}/repo"
  export GIT_CONFIG_GLOBAL=/dev/null
  mkdir -p "$fixture"
  git -C "$fixture" init --quiet
  git -C "$fixture" config user.email ci-skills-tests@example.invalid
  git -C "$fixture" config user.name "ci-skills tests"
  git -C "$fixture" config core.hooksPath "${BATS_TEST_TMPDIR}/no-hooks"
  printf 'base\n' >"$fixture/README.md"
  git -C "$fixture" add README.md
  git -C "$fixture" commit --quiet -m base
  base_branch=$(git -C "$fixture" branch --show-current)
  list="${BATS_TEST_TMPDIR}/staged"
}

copy_bless_fixture() {
  mkdir -p "$fixture/.githooks" "$fixture/lib/bash/automation" \
    "$fixture/lib/bash/core" "$fixture/ci-skills/lib/bash/core"
  cp "$repo_root/Makefile" "$repo_root/bless.sh" "$fixture/"
  cp "$repo_root/.githooks/pre-commit" "$fixture/.githooks/"
  cp "$repo_root/lib/bash/automation/bless.bash" "$fixture/lib/bash/automation/"
  cp "$repo_root/lib/bash/core/"*.bash "$fixture/lib/bash/core/"
  cp "$repo_root/ci-skills/lib/bash/core/runtime.bash" \
    "$fixture/ci-skills/lib/bash/core/runtime.bash"
}

staged() {
  git -C "$fixture" diff --cached --name-only --diff-filter=ACMR -z >"$list"
}

@test 'select prints only the staged paths that match a pattern' {
  printf 'a\n' >"$fixture/a.py"
  printf 'b\n' >"$fixture/b.md"
  git -C "$fixture" add a.py b.md
  staged
  run bash -c 'source "$1"; bless_select "$2" "*.py" | tr "\0" "\n"' _ \
    "${repo_root}/lib/bash/automation/bless.bash" "$list"
  [ "$status" -eq 0 ]
  [ "$output" = "a.py" ]
}

@test 'shell selection finds an extensionless script by its shebang and skips other files' {
  mkdir -p "$fixture/bin" "$fixture/index/bin"
  printf '#!/usr/bin/env bash\necho hi\n' >"$fixture/index/bin/tool"
  printf '#!/usr/bin/env python3\n' >"$fixture/index/bin/py"
  printf 'bin/tool\0bin/py\0x.sh\0notes.txt\0' >"$list"
  run bash -c 'source "$1"; bless_select_shell "$2" "$3" | tr "\0" "\n"' _ \
    "${repo_root}/lib/bash/automation/bless.bash" "$fixture/index" "$list"
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'bin/tool\nx.sh')" ]
}

@test 'trailing whitespace in the staged diff fails the whitespace check' {
  printf 'x   \n' >>"$fixture/README.md"
  git -C "$fixture" add README.md
  run bless_check_whitespace "$fixture"
  [ "$status" -eq 1 ]
}

@test 'nothing staged passes without running a check' {
  run "${repo_root}/bless.sh" --help
  [ "$status" -eq 0 ]
  run bless_run "$fixture" staged false
  [ "$status" -eq 0 ]
  [ "$output" = "bless: no files in staged scope" ]
}

@test 'the dry run lists the staged paths and the checks and runs nothing' {
  printf 'x\n' >"$fixture/new.py"
  git -C "$fixture" add new.py
  run bless_run "$fixture" staged true
  [ "$status" -eq 0 ]
  [[ "$output" == *"new.py"* ]]
  [[ "$output" == *"bless: checks: $BLESS_CHECKS"* ]]
}

@test 'base scope selects only committed files changed through HEAD' {
  base=$(git -C "$fixture" rev-parse HEAD)
  printf '{}\n' >"$fixture/changed.json"
  printf 'working only\n' >"$fixture/untracked.txt"
  git -C "$fixture" add changed.json
  git -C "$fixture" commit --quiet -m candidate
  copy_bless_fixture

  run "$fixture/bless.sh" --base "$base" --dry-run

  [ "$status" -eq 0 ]
  [[ "$output" == *"changed.json"* ]]
  [[ "$output" != *"untracked.txt"* ]]
  [[ "$output" == *"bless: checks: $BLESS_CHECKS"* ]]
}

@test 'base scope blocks when the comparison revision is unavailable' {
  copy_bless_fixture
  run "$fixture/bless.sh" --base "$(printf '0%.0s' {1..40})" --dry-run

  [ "$status" -eq 69 ]
  [[ "$output" == *'bless cannot resolve base revision'* ]]
}

@test 'shell selection includes the tracked hook under .githooks' {
  printf '.githooks/pre-commit\0' >"$list"
  run bash -c 'source "$1"; bless_select_shell "$2" "$3" | tr "\0" "\n"' _ \
    "${repo_root}/lib/bash/automation/bless.bash" "${BATS_TEST_TMPDIR}/index" "$list"
  [ "$status" -eq 0 ]
  [ "$output" = ".githooks/pre-commit" ]
}

@test 'an empty JSON file fails the json check' {
  mkdir -p "${BATS_TEST_TMPDIR}/index"
  : >"${BATS_TEST_TMPDIR}/index/empty.json"
  printf 'empty.json\0' >"$list"
  run bless_check_json "$fixture" "${BATS_TEST_TMPDIR}/index" "$list" ''
  [ "$status" -eq 1 ]
}

@test 'the snapshot holds the staged bytes, not the working copy' {
  printf 'staged\n' >"$fixture/README.md"
  git -C "$fixture" add README.md
  printf 'working\n' >"$fixture/README.md"
  git -C "$fixture" checkout-index --all --prefix="${BATS_TEST_TMPDIR}/index/"
  [ "$(cat "${BATS_TEST_TMPDIR}/index/README.md")" = "staged" ]
}

@test 'make bless checks staged bytes and install-hooks enables the hook' {
  copy_bless_fixture
  printf '{}\n' >"$fixture/valid.json"
  git -C "$fixture" add valid.json
  run make -C "$fixture" bless
  [ "$status" -eq 0 ]
  [ "$(git -C "$fixture" config --local --get core.hooksPath)" = "${BATS_TEST_TMPDIR}/no-hooks" ]
  run make -C "$fixture" install-hooks
  [ "$status" -eq 0 ]
  [ "$(git -C "$fixture" config --local --get core.hooksPath)" = .githooks ]
  run git -C "$fixture" commit -m 'verify installed hook'
  [ "$status" -eq 0 ]
  [[ "$output" == *'bless: json ok'* ]]
  : >"$fixture/invalid.json"
  git -C "$fixture" add invalid.json
  run git -C "$fixture" commit -m 'reject invalid staged JSON'
  [ "$status" -ne 0 ]
  [[ "$output" == *'bless: json FAILED'* ]]
}

@test 'all scope includes a nonignored untracked working file' {
  printf 'untracked\n' >"$fixture/untracked.txt"
  run bless_run "$fixture" all true
  [ "$status" -eq 0 ]
  [[ "$output" == *'untracked.txt'* ]]
}

@test 'staged Bash source cycle fails the source graph check' {
  printf 'source "b.bash"\n' >"$fixture/a.bash"
  printf 'source "a.bash"\n' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$repo_root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Bash source cycle includes'* ]]
}

@test 'changed staged Bash rejects an undeclared dynamic source but all scope accepts inherited files' {
  # shellcheck disable=SC2016 # this literal becomes fixture Bash source.
  printf 'source "$DEPENDENCY"\n' >"$fixture/dynamic.bash"
  git -C "$fixture" add dynamic.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$repo_root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'needs a shellcheck source annotation'* ]]
  git -C "$fixture" commit --quiet -m 'inherited dynamic source'
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic all' _ \
    "$fixture" "$repo_root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 0 ]
}

@test 'annotated dynamic Bash sources still expose a staged cycle' {
  # shellcheck disable=SC2016 # these literals become fixture Bash source.
  printf '# shellcheck source=b.bash\nsource "$TARGET"\n' >"$fixture/a.bash"
  # shellcheck disable=SC2016 # this literal becomes fixture Bash source.
  printf '# shellcheck source=a.bash\nsource "$TARGET"\n' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$repo_root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Bash source cycle includes'* ]]
}

@test 'sourceable bless restores caller EXIT trap and cleans its snapshot on return' {
  marker="${BATS_TEST_TMPDIR}/caller-exit"
  # shellcheck disable=SC2016 # the nested Bash expands its positional arguments.
  run env TMPDIR="$BATS_TEST_TMPDIR" bash -c '
    # shellcheck source=lib/bash/automation/bless.bash
    source "$1"
    trap '\''printf fired > "$2"'\'' EXIT
    bless_run "$3" staged true
    [[ $(trap -p EXIT) == *"printf fired"* ]] || exit 2
    shopt -s nullglob
    leftovers=("$4"/bless.*)
    ((${#leftovers[@]} == 0))
  ' _ "$repo_root/lib/bash/automation/bless.bash" "$marker" "$fixture" "$BATS_TEST_TMPDIR"
  [ "$status" -eq 0 ]
  [ "$(cat "$marker")" = fired ]
}

@test 'plain make lists only the four blessing commands without installing hooks' {
  cp "$repo_root/Makefile" "$fixture/Makefile"
  run make --no-print-directory -C "$fixture"
  [ "$status" -eq 0 ]
  [[ "$output" == *'  make bless '* ]]
  [[ "$output" == *'  make lint '* ]]
  [[ "$output" == *'  make verify-bless '* ]]
  [[ "$output" == *'  make install-hooks '* ]]
  [ "$(printf '%s\n' "$output" | grep -c '^  ')" -eq 4 ]
  [ "$(git -C "$fixture" config --local --get core.hooksPath)" = "${BATS_TEST_TMPDIR}/no-hooks" ]
}

@test 'a merge checks invalid JSON inherited from the incoming branch' {
  git -C "$fixture" checkout -qb incoming
  : >"$fixture/incoming.json"
  git -C "$fixture" add incoming.json
  git -C "$fixture" commit --quiet -m incoming
  git -C "$fixture" checkout -q "$base_branch"
  printf 'local\n' >"$fixture/local.txt"
  git -C "$fixture" add local.txt
  git -C "$fixture" commit --quiet -m local
  git -C "$fixture" merge --no-commit --no-ff incoming >/dev/null
  copy_bless_fixture
  run make --no-print-directory -C "$fixture" bless
  [ "$status" -ne 0 ]
  [[ "$output" == *'bless: json FAILED'* ]]
}

@test 'Bash 3.2 entry reexecutes Bash 5 when PATH prefers bin' {
  run env PATH=/bin:/usr/bin /bin/bash "$repo_root/bless.sh" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *'--log-format FORMAT'* ]]
}

@test 'shared logging flags produce JSON Lines with the selected run ID' {
  log_file="${BATS_TEST_TMPDIR}/bless.jsonl"
  run "$repo_root/bless.sh" --dry-run --log-format json --log-level info \
    --log-file "$log_file" --run-id fixture-run
  [ "$status" -eq 0 ]
  [ "$(jq -r 'select(.runId == "fixture-run") | .event' "$log_file" | wc -l | tr -d ' ')" -eq 2 ]
}

@test 'invalid logging values return usage status' {
  run "$repo_root/bless.sh" --log-level invalid
  [ "$status" -eq 64 ]
  run "$repo_root/bless.sh" --log-format
  [ "$status" -eq 64 ]
}

@test 'missing logging flag values reject option tokens without writing a log' {
  log_file="${BATS_TEST_TMPDIR}/invalid.jsonl"
  for option in --log-format --log-level --log-file --run-id; do
    for missing in -- --dry-run; do
      run "$repo_root/bless.sh" --log-file "$log_file" "$option" "$missing"
      [ "$status" -eq 64 ]
      [ ! -e "$log_file" ]
    done
  done
}

@test 'runtime parity checks staged bytes and rejects a staged package mismatch' {
  mkdir -p "$fixture/lib/bash/core" "$fixture/ci-skills/lib/bash/core"
  cp "$repo_root/lib/bash/core/runtime.bash" "$fixture/lib/bash/core/"
  cp "$repo_root/lib/bash/core/runtime.bash" "$fixture/ci-skills/lib/bash/core/"
  git -C "$fixture" add lib/bash/core/runtime.bash ci-skills/lib/bash/core/runtime.bash
  git -C "$fixture" checkout-index --all --prefix="${BATS_TEST_TMPDIR}/matching/"
  printf 'drift\n' >"$fixture/ci-skills/lib/bash/core/runtime.bash"
  run bless_check_runtime_sync "$fixture" "${BATS_TEST_TMPDIR}/matching" "$list" '' staged
  [ "$status" -eq 0 ]
  git -C "$fixture" add ci-skills/lib/bash/core/runtime.bash
  git -C "$fixture" checkout-index --all --prefix="${BATS_TEST_TMPDIR}/mismatched/"
  run bless_check_runtime_sync "$fixture" "${BATS_TEST_TMPDIR}/mismatched" "$list" '' staged
  [ "$status" -eq 1 ]
  [[ "$output" == *'copy lib/bash/core/runtime.bash to ci-skills/lib/bash/core/runtime.bash'* ]]
}

@test 'staged environment name is read from the index snapshot' {
  printf 'name: staged-env\n' >"$fixture/environment.yml"
  git -C "$fixture" add environment.yml
  printf 'name: working-env\n' >"$fixture/environment.yml"
  mkdir -p "$fixture/index" "$fixture/conda/envs/staged-env/bin"
  git -C "$fixture" checkout-index --all --prefix="$fixture/index/"
  cat >"$fixture/fake-conda" <<EOF
#!/usr/bin/env bash
printf '%s\\n' '$fixture/conda'
EOF
  : >"$fixture/conda/envs/staged-env/bin/python"
  chmod +x "$fixture/fake-conda" "$fixture/conda/envs/staged-env/bin/python"
  export CONDA_EXE="$fixture/fake-conda"
  run bless_python "$fixture/index"
  [ "$status" -eq 0 ]
  [ "$output" = "$fixture/conda/envs/staged-env/bin/python" ]
}

@test 'an unknown argument is a usage error' {
  run "${repo_root}/bless.sh" --nope
  [ "$status" -eq 64 ]
}
