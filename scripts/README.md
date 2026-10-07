# Repository structure

Read from the tree on 2026-10-07; entries marked planned do not exist yet and
name the phase that creates them. The phase documents under `docs/phases/`
own the design.

`scripts/` holds development and build executables; skill commands live in
`bin/`. Both call reusable functions in `lib/bash/` or `lib/python/`, with
modules separated by purpose. Dependencies flow from executables into
libraries, never from libraries into executables, and a local build script
does not import CI-specific behavior.

```text
ci-skills/                     # the one installable skill (Agent Skills layout)
├── SKILL.md                   # agent entry point and router
├── tools.json                 # generated command manifest (tools/render_manifest.py)
├── bin/                       # skill commands: thin *.py mains, ci-api, ci-binary-build; _bootstrap.py
├── lib/python/core/           # the Python library (catalog, cli, access, collectors, GitLab actions)
├── lib/bash/core/             # generic Bash helpers any executable may source (runtime.bash)
├── lib/bash/api/              # the library behind bin/ci-api
├── lib/bash/automation/       # the library behind bin/ci-binary-build
├── references/                # access.md, project-binding.md; vendor/<name>/ planned (CI09)
└── benchmarks/
bin/                           # repository-root adapters to ci-skills/bin/
tools/                         # repository maintenance mains; tools/skillkit/ planned
scripts/                       # development and build executables
├── check.sh                   # its CI library was deleted on purpose; not runnable
└── toolchain/                 # install.sh, conda.sh; their lib/bash/toolchain/ library is not in the tree
tests/{python,bash,acceptance} # pytest, bats, the live receipt contract and sanitized receipts
schemas/                       # record schemas (CI07-SCHEMA)
vendor/                        # vendored skills and the one lock, planned (CI05-VENDOR)
docs/                          # index README.md; phases/ CI01..CI11; dated plans/, reviews/, brainstorm/
```
