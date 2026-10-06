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
  cp -R "$root/scripts" "$root/ci-skills" "$fixture/"
  cp "$root/Makefile" "$root/bless.sh" "$root/environment.yml" \
    "$root/toolchain-dependencies.json" "$fixture/"
  plan="$("$fixture/scripts/dev.sh" hooks --dry-run --json)"
  fingerprint="$(jq -er .plan_fingerprint <<<"$plan")"
  run "$fixture/scripts/dev.sh" hooks --apply --confirm-install "$fingerprint" --json
  [ "$status" -eq 0 ]
  [[ "$output" == *'"status": "PASS"'* ]]
  [ -x "$fixture/.git/hooks/pre-commit" ]
  grep -Fq 'ci-skills-bless-v1' "$fixture/.git/hooks/pre-commit"
}

@test 'hook apply refuses a stale plan without installing a hook' {
  cp -R "$root/scripts" "$root/ci-skills" "$fixture/"
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
      "$root/scripts/bash/core/bless.bash" "$path"
    [ "$status" -eq 0 ]
  done
  run bash -c 'source "$1"; ci_bless_secret_path "$2"' _ \
    "$root/scripts/bash/core/bless.bash" AGENCY.md
  [ "$status" -eq 1 ]
}

@test 'changed shell file without source annotation is rejected' {
  printf '%s\n' '#!/usr/bin/env bash' 'source "missing.bash"' >"$fixture/a.bash"
  git -C "$fixture" add a.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/scripts/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Missing or extra source annotation'* ]]
}

@test 'changed shell file with a missing dependency is rejected' {
  printf '%s\n' '#!/usr/bin/env bash' '# shellcheck source=missing.bash' \
    'source "missing.bash"' >"$fixture/a.bash"
  git -C "$fixture" add a.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/scripts/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Missing staged Bash dependency'* ]]
}

@test 'changed shell dependency cycle is rejected' {
  printf '%s\n' '# shellcheck source=b.bash' 'source "b.bash"' >"$fixture/a.bash"
  printf '%s\n' '# shellcheck source=a.bash' 'source "a.bash"' >"$fixture/b.bash"
  git -C "$fixture" add a.bash b.bash
  run bash -c 'cd "$1"; source "$2"; ci_source_graph_acyclic staged' _ \
    "$fixture" "$root/scripts/bash/core/source_graph.bash"
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
    "$fixture" "$root/scripts/bash/core/source_graph.bash"
  [ "$status" -eq 1 ]
  [[ "$output" == *'Source annotation disagrees'* ]]
}

@test 'hook installer preserves a foreign pre-commit hook' {
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' >"$fixture/.git/hooks/pre-commit"
  chmod +x "$fixture/.git/hooks/pre-commit"
  run bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/scripts/bash/core/hooks.bash" "$fixture"
  [ "$status" -eq 73 ]
  grep -Fq 'exit 0' "$fixture/.git/hooks/pre-commit"
}

@test 'installed hook rejects a checkout without bless' {
  bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/scripts/bash/core/hooks.bash" "$fixture"
  printf '%s\n' data >"$fixture/file.txt"
  git -C "$fixture" add file.txt
  run git -C "$fixture" commit -qm 'fixture commit'
  [ "$status" -ne 0 ]
  [[ "$output" == *'bless.sh is unavailable'* ]]
}

@test 'installed hook commits staged JSON while rejecting staged invalid JSON' {
  cp "$root/bless.sh" "$root/.markdownlint-cli2.yaml" "$root/.gitleaks.toml" \
    "$root/environment.yml" "$root/toolchain-dependencies.json" "$fixture/"
  cp -R "$root/scripts" "$root/schemas" "$root/ci-skills" "$fixture/"
  bash -c 'source "$1"; ci_hooks_install "$2"' _ \
    "$root/scripts/bash/core/hooks.bash" "$fixture"
  printf '{"value":1}\n' >"$fixture/example.json"
  git -C "$fixture" add example.json
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
    '{"kind":"bless_result","schema_version":"1.0","status":"PASS","scope":"staged","summary":{"selected_files":1,"check_count":1,"failed_checks":1},"checks":[{"path":"a","check":"json","status":"FAIL"}]}' \
    >"$fixture/false-pass.json"
  run check-jsonschema --schemafile "$root/schemas/commands/results/bless.schema.json" \
    "$fixture/false-pass.json"
  [ "$status" -ne 0 ]
}
