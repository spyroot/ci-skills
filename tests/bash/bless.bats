#!/usr/bin/env bats
# bless.sh and ci-skills/lib/bash/bless/bless.bash: the pre-commit hook.
# The cases here need only bash and git; the linters bless calls are covered by
# their own configuration and by running bless on a real commit.

setup() {
  repo_root="$(cd -- "${BATS_TEST_DIRNAME}/../.." && pwd -P)"
  # shellcheck source=ci-skills/lib/bash/core/runtime.bash
  source "${repo_root}/ci-skills/lib/bash/core/runtime.bash"
  # shellcheck source=ci-skills/lib/bash/bless/bless.bash
  source "${repo_root}/ci-skills/lib/bash/bless/bless.bash"
  fixture="${BATS_TEST_TMPDIR}/repo"
  mkdir -p "$fixture"
  git -C "$fixture" init --quiet
  git -C "$fixture" config user.email ci-skills-tests@example.invalid
  git -C "$fixture" config user.name "ci-skills tests"
  git -C "$fixture" config core.hooksPath "${BATS_TEST_TMPDIR}/no-hooks"
  printf 'base\n' >"$fixture/README.md"
  git -C "$fixture" add README.md
  git -C "$fixture" commit --quiet -m base
  list="${BATS_TEST_TMPDIR}/staged"
}

staged() {
  git -C "$fixture" diff --cached --name-only --diff-filter=ACMR -z >"$list"
}

@test 'select prints only the staged paths that match a pattern' {
  printf 'a\n' >"$fixture/a.py"
  printf 'b\n' >"$fixture/b.md"
  git -C "$fixture" add a.py b.md
  staged
  run bash -c 'source "$1"; source "$2"; bless_select "$3" "*.py" | tr "\0" "\n"' _ \
    "${repo_root}/ci-skills/lib/bash/core/runtime.bash" \
    "${repo_root}/ci-skills/lib/bash/bless/bless.bash" "$list"
  [ "$status" -eq 0 ]
  [ "$output" = "a.py" ]
}

@test 'shell selection finds an extensionless script by its shebang and skips other files' {
  mkdir -p "$fixture/bin" "$fixture/index/bin"
  printf '#!/usr/bin/env bash\necho hi\n' >"$fixture/index/bin/tool"
  printf '#!/usr/bin/env python3\n' >"$fixture/index/bin/py"
  printf 'bin/tool\0bin/py\0x.sh\0notes.txt\0' >"$list"
  run bash -c 'source "$1"; source "$2"; bless_select_shell "$3" "$4" | tr "\0" "\n"' _ \
    "${repo_root}/ci-skills/lib/bash/core/runtime.bash" \
    "${repo_root}/ci-skills/lib/bash/bless/bless.bash" "$fixture/index" "$list"
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
  run bless_run "$fixture" false
  [ "$status" -eq 0 ]
  [ "$output" = "bless: nothing staged" ]
}

@test 'the dry run lists the staged paths and the checks and runs nothing' {
  printf 'x\n' >"$fixture/new.py"
  git -C "$fixture" add new.py
  run bless_run "$fixture" true
  [ "$status" -eq 0 ]
  [[ "$output" == *"new.py"* ]]
  [[ "$output" == *"bless: checks: $BLESS_CHECKS"* ]]
}

@test 'shell selection includes the tracked hook under .githooks' {
  printf '.githooks/pre-commit\0' >"$list"
  run bash -c 'source "$1"; source "$2"; bless_select_shell "$3" "$4" | tr "\0" "\n"' _ \
    "${repo_root}/ci-skills/lib/bash/core/runtime.bash" \
    "${repo_root}/ci-skills/lib/bash/bless/bless.bash" "${BATS_TEST_TMPDIR}/index" "$list"
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

@test 'the tracked pre-commit hook is executable and runs bless.sh on the index' {
  hook="${repo_root}/.githooks/pre-commit"
  [ -x "$hook" ]
  # shellcheck disable=SC2016 # the hook's own text, matched literally
  grep -qF 'exec "$repo_root/bless.sh" --staged' "$hook"
}

@test 'make install-hooks activates .githooks and make bless reaches it' {
  run make -C "$repo_root" -n bless
  [ "$status" -eq 0 ]
  [[ "$output" == *'config --local core.hooksPath .githooks'* ]]
  [[ "$output" == *'./bless.sh --staged'* ]]
}

@test 'an unknown argument is a usage error' {
  run "${repo_root}/bless.sh" --nope
  [ "$status" -eq "$CI_EXIT_USAGE" ]
}
