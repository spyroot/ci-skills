## Repository Structure

```text
ci-skills/
├── skills/k8s-admin-diagnostics/
│   ├── SKILL.md              # agent entry point
│   ├── scripts/bash/core/    # reusable bash behavior
|   |---scripts/python/core/  # reusable Python behavior
│   ├── references/           # skill reference material
│   └── tools.json            # generated command manifest
├── tools/                    # repository maintenance commands
├── tests/                    # pytest cases
├── acceptance/               # live receipt contract and sanitized receipts
├── docs/field-notes.md
├── .github/workflows/validate.yml
├── standards-binding.yaml
├── requirements.txt
└── target.toml.template
```