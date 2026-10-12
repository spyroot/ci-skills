#!/usr/bin/env bats

setup() {
  tool="${BATS_TEST_DIRNAME}/../bin/ci-binary-build"
  repo="${BATS_TEST_TMPDIR}/source"
  mkdir -p "$repo" "${BATS_TEST_TMPDIR}/mock"
  git -C "$repo" init -q
  git -C "$repo" config user.name Fixture
  git -C "$repo" config user.email fixture@example.invalid
  printf 'FROM scratch\n' > "$repo/Dockerfile"
  git -C "$repo" add Dockerfile
  git -C "$repo" commit -qm 'Fixture Dockerfile'
  commit=$(git -C "$repo" rev-parse HEAD)
  spec="${BATS_TEST_TMPDIR}/buildconfig.yaml"
  cat > "$spec" <<EOF_SPEC
apiVersion: build.openshift.io/v1
kind: BuildConfig
metadata:
  name: fixture-build
  namespace: fixture-ns
  labels:
    example.invalid/source-commit: $commit
spec:
  source:
    type: Binary
    binary: {}
  strategy:
    type: Docker
    dockerStrategy:
      dockerfilePath: Dockerfile
  output:
    to:
      kind: DockerImage
      name: registry.example.invalid/demo/image:candidate
  triggers: []
EOF_SPEC
  export PATH="${BATS_TEST_TMPDIR}/mock:${PATH}"
}

@test 'plan contains an exact commit and fingerprint without calling oc' {
  cat > "${BATS_TEST_TMPDIR}/mock/oc" <<'MOCK'
#!/usr/bin/env bash
exit 99
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/oc"
  run "$tool" --spec "$spec" --source-repo "$repo" \
    --source-commit "$commit" --commit-label example.invalid/source-commit \
    --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *'"mode":"dry-run"'* ]]
  [[ "$output" == *'"fingerprint":"'* ]]
}

@test 'apply is unsupported and cannot call OpenShift' {
  cat > "${BATS_TEST_TMPDIR}/mock/oc" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' 'called' > "$CI_TEST_OC_MARKER"
exit 99
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/mock/oc"
  export CI_TEST_OC_MARKER="${BATS_TEST_TMPDIR}/oc-called"
  run "$tool" --spec "$spec" --source-repo "$repo" \
    --source-commit "$commit" --commit-label example.invalid/source-commit \
    --context fixture --timeout 10s --apply --confirm-build wrong
  [ "$status" -eq 64 ]
  [ ! -e "$CI_TEST_OC_MARKER" ]
}

@test 'plan rejects a BuildConfig with a different source commit label' {
  other_commit=0000000000000000000000000000000000000000
  sed "s/$commit/$other_commit/" "$spec" > "${BATS_TEST_TMPDIR}/wrong.yaml"
  run "$tool" --spec "${BATS_TEST_TMPDIR}/wrong.yaml" --source-repo "$repo" \
    --source-commit "$commit" --commit-label example.invalid/source-commit \
    --log-level error
  [ "$status" -eq 65 ]
}
