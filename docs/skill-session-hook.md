# Claude skill hook design

Install `ci-skills/` at `~/.claude/skills/ci-skills` (user) or
`.claude/skills/ci-skills` (project). Codex uses `~/.codex/skills/ci-skills`
or `.agents/skills/ci-skills` at the same scopes.

At install time, record the source checkout and hook command in
`~/.claude/settings.json` (user) or `.claude/settings.json` (project).
On `SessionStart`, that command refreshes the skill only when the source
changed, then prints
`{"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true}}`.
Claude rescans skills before the first prompt. No change means no reload;
failure means an error, with no success response. The hook does not run a
capability. After discovery, the agent selects a command from `SKILL.md`;
that command resolves its target at runtime from the selected target file.
For a failed mutation such as runner creation, the command reads provider state
before any retry; `PostToolUseFailure` may only point the agent to that result.
