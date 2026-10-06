# Repository structure

Read from the tree on 2026-10-06; entries marked planned do not exist yet and
name the phase that creates them. The phase documents under `docs/phases/`
own the design.

```text
ci-skills/                     # the one installable skill (Agent Skills layout)
├── SKILL.md                   # agent entry point and router
├── tools.json                 # generated command manifest (tools/render_manifest.py)
├── bin/                       # thin mains: *.py over lib/core, ci-api and ci-binary-build over lib/bash
├── lib/core/                  # the Python library (catalog, cli, access, collectors, GitLab actions)
├── lib/bash/{core,ci,automation}/   # the Bash libraries
├── references/                # access.md, project-binding.md; vendor/<name>/ planned (CI09)
└── benchmarks/
tools/                         # repository maintenance mains; tools/skillkit/ planned (CI07, CI05, CI09, CI01)
scripts/                       # dev entrypoints: check.sh and scripts/bash/core/
tests/{python,bash,acceptance} # pytest, bats, the live receipt contract and sanitized receipts
schemas/                       # record schemas, planned (CI07-SCHEMA); empty today
vendor/                        # vendored skills and the one lock, planned (CI05-VENDOR)
docs/phases/                   # CI01-CATALOG ... CI11-TOOLS
```
