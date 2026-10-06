# Repository structure

Read from the tree on 2026-10-06. The phase documents under `docs/phases/`
own the design; `TEAM_GUIDE.md` (local, ignored) owns the project rules.

```text
ci-skills/                     # the one installable skill (Agent Skills layout)
├── SKILL.md                   # agent entry point and router
├── tools.json                 # generated command manifest (tools/render_manifest.py)
├── bin/                       # thin mains: *.py over lib/core, ci-api and ci-binary-build over lib/bash
├── lib/core/                  # the Python library (catalog, cli, access, collectors, GitLab actions)
├── lib/bash/{core,ci,automation}/   # the Bash libraries
├── references/                # access.md, project-binding.md; vendor/<name>/ for upstream knowledge
└── benchmarks/
tools/                         # repository maintenance mains; tools/skillkit/ is their library
scripts/                       # dev entrypoints: check.sh and scripts/bash/core/
tests/{python,bash,acceptance} # pytest, bats, the live receipt contract and sanitized receipts
schemas/                       # record schemas (CI07-SCHEMA)
vendor/                        # vendored skills and the one vendor lock (CI05-VENDOR)
docs/phases/                   # CI01-CATALOG ... CI11-TOOLS
```
