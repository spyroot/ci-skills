---
name: ci-skills
description: Reusable Git API inspection and OpenShift Binary BuildConfig planning tools for CI agents. Use for authenticated GitHub or GitLab reads, selected source content, and exact-commit build plans.
---

# CI Skills

Use the installed commands under `bin/` for their supported operations. Supply
repository, endpoint, ref, cluster context, and namespace from the current task
or project binding. Do not reconstruct the same API read with one-off `gh api`,
`glab api`, `jq`, `base64`, `grep`, or `sed` pipelines when `ci-api` supports it.

- `bin/ci-api --help`: authenticated, read-only GitHub/GitLab API requests.
  Select a JSON field, decode base64 file content, or read a bounded plain-text
  response such as a GitLab job trace. It accepts a caller-selected endpoint;
  it does not select a project or host for GitLab.
- `bin/ci-binary-build --help`: validate a caller-supplied OpenShift Binary
  BuildConfig and print a plan tied to an exact Git commit. It does not apply
  the BuildConfig or start a build.
- `install.sh --help`: install this directory as a Codex skill symlink.
- `scripts/check.sh --help`: inspect or run the repository validation gate.
  Live validation requires the project-approved Kubernetes job.

If a needed action is missing, report it or extend the shared tool in this
repository when the current request authorizes that change. Do not disguise a
new action as a raw API write. Validate in the project-approved Kubernetes or
CI route; local inspection is not authoritative gate evidence.

This skill loads no source code from another repository or skill. Its command
line dependencies are listed in `README.md`.
