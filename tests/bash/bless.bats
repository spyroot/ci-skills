#!/usr/bin/env bats
# Author Mustafa Bayramov mbayramo@cisco.com / spyroot@gmail.com

setup() {
  root="${BATS_TEST_DIRNAME}/../.."
  fixture="${BATS_TEST_TMPDIR}/source"
  mkdir -p "$fixture"
  git -C "$fixture" init -q
  git -C "$fixture" config user.name ci-skills-tests
  git -C "$fixture" config user.email ci-skills-tests@example.invalid
}

@test 'development install defaults to a plan and does not create a hook' {
  run "$root/scripts/dev.sh" hooks --dry-run --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status": "PLANNED"'* ]]
  [[ "$output" == *'"plan_fingerprint": '* ]]
}

@test 'hook dry-run leaves the Git hook directory unchanged' {
  run bash -c 'source "$1"; ci_hooks_plan "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 0 ]
  [[ "$output" == *'PLAN install hook:'* ]]
  [ ! -e "$fixture/.git/hooks/pre-commit" ]
  [ -z "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
}

@test 'unchanged toolchain produces the same reviewed plan fingerprint' {
  first="$("$root/scripts/dev.sh" toolchain --dry-run --json)"
  second="$("$root/scripts/dev.sh" toolchain --dry-run --json)"
  [ "$(jq -r .plan_fingerprint <<<"$first")" = \
    "$(jq -r .plan_fingerprint <<<"$second")" ]
  [ "$(jq -r .detail <<<"$first")" = "$(jq -r .detail <<<"$second")" ]
}

@test 'development install refuses apply without confirmation' {
  run "$root/scripts/dev.sh" hooks --apply --json
  [ "$status" -eq 64 ]
  [[ "$output" == *'"status": "FAIL"'* ]]
  [[ "$output" == *'SAFE_NEXT_STEP:'* ]]
}

@test 'hook apply consumes the reviewed plan and reads back an installed hook' {
  cp -R "$root/scripts" "$root/lib" "$root/ci-skills" "$fixture/"
  cp "$root/Makefile" "$root/bless.sh" "$root/environment.yml" \
    "$root/toolchain-dependencies.json" "$fixture/"
  plan="$("$fixture/scripts/dev.sh" hooks --dry-run --json)"
  fingerprint="$(jq -er .plan_fingerprint <<<"$plan")"
  run "$fixture/scripts/dev.sh" hooks --apply --confirm-install "$fingerprint" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status": "PASS"'* ]]
  [[ "$output" == *'"cleanup_status": "PASS"'* ]]
  [ -x "$fixture/.git/hooks/pre-commit" ]
  grep -Fq 'ci-skills-bless-v1' "$fixture/.git/hooks/pre-commit"
  grep -Fq 'exec "$root/bless.sh" --staged' "$fixture/.git/hooks/pre-commit"
}

@test 'hook apply refuses a stale plan without installing a hook' {
  cp -R "$root/scripts" "$root/lib" "$root/ci-skills" "$fixture/"
  cp "$root/Makefile" "$root/bless.sh" "$root/environment.yml" \
    "$root/toolchain-dependencies.json" "$fixture/"
  plan="$("$fixture/scripts/dev.sh" hooks --dry-run --json)"
  fingerprint="$(jq -er .plan_fingerprint <<<"$plan")"
  printf '\n' >>"$fixture/toolchain-dependencies.json"
  run "$fixture/scripts/dev.sh" hooks --apply --confirm-install "$fingerprint" --json
  [ "$status" -eq 64 ]
  [[ "$output" == *'"status": "FAIL"'* ]]
  [[ "$output" == *'"safe_next_step": '* ]]
  [ ! -e "$fixture/.git/hooks/pre-commit" ]
}

@test 'secret-class paths are refused while a near match is allowed' {
  for path in CODEX_HANDOFF.md CODEX_HANDOFF.diff CLAUDE_REVIEW.diff \
    .AGENTS.md .AGENT_HANDOFF.patch docs/TEAM_GUIDE.md \
    .ci-skills/target.toml .internal/queue/task.yaml; do
    run bash -c 'source "$1"; ci_bless_secret_path "$2"' _ \
      "$root/lib/bash/automation/bless.bash" "$path"
    [ "$status" -eq 0 ]
  done
  run bash -c 'source "$1"; ci_bless_secret_path "$2"' _ \
    "$root/lib/bash/automation/bless.bash" AGENCY.md
  [ "$status" -eq 1 ]
}

@test 'changed shell file without source annotation is rejected' {
  printf '%s\n' '#!/usr/bin/env bash' 'source "missing.bash"' >"$fixture/a.bash"
  git -C "$fixture" add a.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Missing or extra source annotation'* ]]
}

@test 'changed shell file with a missing dependency is rejected' {
  printf '%s\n' '#!/usr/bin/env bash' '# shellcheck source=missing.bash' \
    'source "missing.bash"' >"$fixture/a.bash"
  git -C "$fixture" add a.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Missing staged Bash dependency'* ]]
}

@test 'staged deletion of a sourced Bash target is rejected for an unchanged caller' {
  printf '%s\n' '# shellcheck source=b.bash' 'source "b.bash"' >"$fixture/a.bash"
  printf '%s\n' '#!/usr/bin/env bash' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash
  git -C "$fixture" -c core.hooksPath=/dev/null commit -qm 'baseline'
  git -C "$fixture" rm -q b.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Missing staged Bash dependency b.bash from a.bash'* ]]
}

@test 'changed shell dependency cycle is rejected' {
  printf '%s\n' '# shellcheck source=b.bash' 'source "b.bash"' >"$fixture/a.bash"
  printf '%s\n' '# shellcheck source=a.bash' 'source "a.bash"' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Bash source cycle includes'* ]]
}

@test 'source annotation that disagrees with a literal target is rejected' {
  printf '%s\n' '#!/usr/bin/env bash' '# shellcheck source=b.bash' \
    'source "a.bash"' >"$fixture/c.bash"
  printf '%s\n' '#!/usr/bin/env bash' >"$fixture/a.bash"
  printf '%s\n' '#!/usr/bin/env bash' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash c.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/lib/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Source annotation disagrees'* ]]
}

@test 'hook installer preserves a foreign pre-commit hook' {
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"$fixture/.git/hooks/pre-commit"
  chmod +x "$fixture/.git/hooks/pre-commit"
  run bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 73 ]
  grep -Fq 'exit 0' "$fixture/.git/hooks/pre-commit"
}

@test 'hook installer second run is a verified no-op with no write call' {
  bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  mkdir -p "$fixture/fake-bin"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 99' >"$fixture/fake-bin/ln"
  chmod +x "$fixture/fake-bin/ln"
  run env PATH="$fixture/fake-bin:$PATH" bash -c \
    'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 0 ]
  [[ "$output" == *'NO_OP hook already installed:'* ]]
  [ -z "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
}

@test 'hook installer removes its linked hook when read-back mismatches' {
  mkdir -p "$fixture/fake-bin"
  cat >"$fixture/fake-bin/ln" <<'FAKE'
#!/usr/bin/env bash
/bin/ln "$@" || exit
printf 'corruption\n' >>"$2"
FAKE
  chmod +x "$fixture/fake-bin/ln"
  run env PATH="$fixture/fake-bin:$PATH" bash -c \
    'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 1 ]
  [[ "$output" == *'read-back mismatches'* ]]
  [ ! -e "$fixture/.git/hooks/pre-commit" ]
  [ -z "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
}

@test 'hook installer removes its linked hook after HUP INT and TERM' {
  mkdir -p "$fixture/fake-bin"
  cat >"$fixture/fake-bin/ln" <<'FAKE'
#!/usr/bin/env bash
/bin/ln "$@" || exit
kill -s "$CI_TEST_SIGNAL" "$PPID"
FAKE
  chmod +x "$fixture/fake-bin/ln"
  for signal_status in HUP:129 INT:130 TERM:143; do
    signal="${signal_status%%:*}"
    expected="${signal_status##*:}"
    run env PATH="$fixture/fake-bin:$PATH" CI_TEST_SIGNAL="$signal" bash -c \
      'source "$1"; ci_hooks_install "$2"' _ \
      "$root/lib/bash/automation/hooks.bash" "$fixture"
    [ "$status" -eq "$expected" ]
    [ ! -e "$fixture/.git/hooks/pre-commit" ]
    [ -z "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
  done
}

@test 'hook installer cleans its linked hook when the bounded step times out' {
  mkdir -p "$fixture/fake-bin"
  cat >"$fixture/fake-bin/ln" <<'FAKE'
#!/usr/bin/env bash
/bin/ln "$@" || exit
sleep 5
FAKE
  chmod +x "$fixture/fake-bin/ln"
  run env PATH="$fixture/fake-bin:$PATH" timeout 1 bash -c \
    'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 124 ]
  [ ! -e "$fixture/.git/hooks/pre-commit" ]
  [ -z "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
}

@test 'required cleanup failure overrides successful hook installation' {
  mkdir -p "$fixture/fake-bin"
  cat >"$fixture/fake-bin/rm" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  *ci-skills-pre-commit.*) exit 1 ;;
esac
exec /bin/rm "$@"
FAKE
  chmod +x "$fixture/fake-bin/rm"
  run env PATH="$fixture/fake-bin:$PATH" bash -c \
    'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  [ "$status" -eq 1 ]
  [[ "$output" == *'CLEANUP_FAIL: ci_runtime_cleanup_file'* ]]
  [ -e "$fixture/.git/hooks/pre-commit" ]
  [ -n "$(find "$fixture/.git/hooks" -name 'ci-skills-pre-commit.*' -print)" ]
}

@test 'development apply reports observed hook cleanup failure' {
  cp -R "$root/scripts" "$root/lib" "$root/ci-skills" "$fixture/"
  cp "$root/Makefile" "$root/bless.sh" "$root/environment.yml" \
    "$root/toolchain-dependencies.json" "$fixture/"
  plan="$("$fixture/scripts/dev.sh" hooks --dry-run --json)"
  fingerprint="$(jq -er .plan_fingerprint <<<"$plan")"
  mkdir -p "$fixture/fake-bin"
  cat >"$fixture/fake-bin/rm" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  *ci-skills-pre-commit.*) exit 1 ;;
esac
exec /bin/rm "$@"
FAKE
  chmod +x "$fixture/fake-bin/rm"
  result="$BATS_TEST_TMPDIR/dev-cleanup-failure.json"
  diagnostics="$BATS_TEST_TMPDIR/dev-cleanup-failure.err"
  run env PATH="$fixture/fake-bin:$PATH" CI_LOG_LEVEL=error bash -c \
    '"$1" hooks --apply --confirm-install "$2" --json >"$3" 2>"$4"' _ \
    "$fixture/scripts/dev.sh" "$fingerprint" "$result" "$diagnostics"
  [ "$status" -eq 1 ]
  grep -Fq 'CLEANUP_FAIL' "$diagnostics"
  jq -e '.status == "FAIL" and .cleanup_status == "FAIL" and
    (.evidence_path | type == "string" and length > 0)' "$result"
  [ -d "$(jq -r .evidence_path "$result")" ]
  run check-jsonschema --schemafile "$root/schemas/commands/results/dev.schema.json" \
    "$result"
  [ "$status" -eq 0 ]
  /bin/rm -rf -- "$(jq -r .evidence_path "$result")"
}

@test 'shared cleanup stack is LIFO, continues after failure, and runs once' {
  journal="$fixture/cleanup-journal"
  run env CI_TEST_JOURNAL="$journal" bash -c '
    source "$1"
    ci_runtime_lifecycle_begin
    cleanup_action() {
      printf "%s\n" "$1" >>"$CI_TEST_JOURNAL"
      [[ "$1" != fail ]]
    }
    ci_runtime_cleanup_push cleanup_action first
    ci_runtime_cleanup_push cleanup_action fail
    ci_runtime_cleanup_push cleanup_action last
    ci_runtime_cleanup_run || :
    ci_runtime_cleanup_run || :
  ' _ "$root/lib/bash/core/runtime.bash"
  [ "$status" -eq 1 ]
  [ "$(cat "$journal")" = $'last\nfail\nfirst' ]
  [[ "$output" == *'CLEANUP_FAIL: cleanup_action'* ]]
}

@test 'blocked all-scope prerequisite reports retain all scope in JSON and YAML' {
  for output_mode in --json --yaml; do
    run bash -c 'source "$1"; ci_toolchain_conda() { return 1; }; ci_bless_main "$2" --all "$3"' _ \
      "$root/lib/bash/automation/bless.bash" "$fixture" "$output_mode"
    [ "$status" -eq 69 ]
    [[ "$output" == *'"scope": "all"'* ]]
    [[ "$output" == *'"exit_code": 69'* ]]
    [[ "$output" != *'"scope": "staged"'* ]]
  done
}

@test 'installed hook rejects a checkout without bless' {
  bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/lib/bash/automation/hooks.bash" "$fixture"
  printf '%s\n' data >"$fixture/file.txt"
  git -C "$fixture" add file.txt
  run git -C "$fixture" commit -qm 'fixture commit'
  [ "$status" -ne 0 ]
  [[ "$output" == *'bless.sh is unavailable'* ]]
}

@test 'installed hook commits staged JSON while rejecting staged invalid JSON' {
  cp "$root/Makefile" "$root/bless.sh" "$root/.markdownlint-cli2.yaml" "$root/.gitleaks.toml" \
    "$root/environment.yml" "$root/toolchain-dependencies.json" "$fixture/"
  cp -R "$root/scripts" "$root/lib" "$root/schemas" "$root/ci-skills" "$fixture/"
  printf '{"value":1}\n' >"$fixture/example.json"
  git -C "$fixture" add example.json
  run make -C "$fixture" bless
  [ "$status" -eq 0 ]
  [ -x "$fixture/.git/hooks/pre-commit" ]
  grep -Fq 'exec "$root/bless.sh" --staged' "$fixture/.git/hooks/pre-commit"
  printf '{"broken":' >"$fixture/example.json"
  run git -C "$fixture" commit -qm 'valid staged content'
  [ "$status" -eq 0 ]
  printf '{"broken":' >"$fixture/invalid.json"
  git -C "$fixture" add invalid.json
  run git -C "$fixture" commit -qm 'invalid staged content'
  [ "$status" -ne 0 ]
  [[ "$output" == *'FAIL json invalid.json'* ]]
}

@test 'bless result schema rejects a passing result with a failing check' {
  printf '%s\n' \
    '{"kind":"bless_result","schema_version":"1.0","status":"PASS","scope":"staged","exit_code":0,"warning_count":0,"cleanup_status":"PASS","evidence_path":null,"summary":{"selected_files":1,"check_count":1,"failed_checks":1},"checks":[{"path":"a","check":"json","status":"FAIL"}]}' \
    >"$fixture/false-pass.json"
  run check-jsonschema --schemafile "$root/schemas/commands/results/bless.schema.json" \
    "$fixture/false-pass.json"
  [ "$status" -ne 0 ]
}

@test 'bless schema rejects cleanup failure without retained evidence' {
  printf '%s\n' \
    '{"kind":"bless_result","schema_version":"1.0","status":"FAIL","scope":"staged","exit_code":1,"warning_count":0,"cleanup_status":"FAIL","evidence_path":null,"safe_next_step":"Inspect cleanup","summary":{"selected_files":0,"check_count":1,"failed_checks":1},"checks":[{"path":"temporary-directory","check":"cleanup","status":"FAIL"}]}' \
    >"$fixture/missing-cleanup-evidence.json"
  run check-jsonschema --schemafile "$root/schemas/commands/results/bless.schema.json" \
    "$fixture/missing-cleanup-evidence.json"
  [ "$status" -ne 0 ]
}

@test 'planned development and bless results record cleanup and match their schemas' {
  dev_result="$BATS_TEST_TMPDIR/dev-result.json"
  bless_result="$BATS_TEST_TMPDIR/bless-result.json"
  CI_LOG_LEVEL=error "$root/scripts/dev.sh" hooks --dry-run --json >"$dev_result"
  jq -e '.status == "PLANNED" and .cleanup_status == "NOT_APPLICABLE" and
    .warning_count == 0 and .evidence_path == null' "$dev_result"
  run check-jsonschema --schemafile "$root/schemas/commands/results/dev.schema.json" \
    "$dev_result"
  [ "$status" -eq 0 ]

  CI_LOG_LEVEL=error bash -c 'source "$1"; ci_bless_main "$2" --staged --dry-run --json' _ \
    "$root/lib/bash/automation/bless.bash" "$fixture" \
    >"$bless_result" 2>"$BATS_TEST_TMPDIR/bless-result.err"
  jq -e '.status == "PLANNED" and .cleanup_status == "PASS" and
    .warning_count == 0 and .evidence_path == null' "$bless_result"
  run check-jsonschema --schemafile "$root/schemas/commands/results/bless.schema.json" \
    "$bless_result"
  [ "$status" -eq 0 ]
}

@test 'cleanup failure changes the final bless result and exit code' {
  result="$BATS_TEST_TMPDIR/failed-cleanup-result.json"
  diagnostics="$BATS_TEST_TMPDIR/failed-cleanup.err"
  run bash -c '
    source "$1"
    ci_runtime_cleanup_dir() { return 1; }
    ci_bless_main "$2" --staged --dry-run --json >"$3" 2>"$4"
  ' _ "$root/lib/bash/automation/bless.bash" "$fixture" "$result" "$diagnostics"
  [ "$status" -eq 1 ]
  grep -Fq 'CLEANUP_FAIL' "$diagnostics"
  jq -e '.status == "FAIL" and .exit_code == 1 and .cleanup_status == "FAIL" and
    .summary.failed_checks == 1 and
    any(.checks[]; .check == "cleanup" and .status == "FAIL") and
    (.evidence_path | type == "string" and length > 0)' "$result"
  [ -d "$(jq -r .evidence_path "$result")" ]
  /bin/rm -rf -- "$(jq -r .evidence_path "$result")"
  run check-jsonschema --schemafile "$root/schemas/commands/results/bless.schema.json" \
    "$result"
  [ "$status" -eq 0 ]
}
