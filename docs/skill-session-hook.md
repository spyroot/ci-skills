# Agent lifecycle hooks for CI Skills

**Recommendation:** install the skill before starting an agent session. A hook
is useful only when a local source changes between sessions or a failed tool
call needs a short recovery hint. Hooks do not run CI Skills capabilities.
The current installer does not register lifecycle hooks; these are options
for a later integration. A refresh hook would need the installer to record a
trusted source path for later sessions.

- `SessionStart`: optional Claude Code refresh from the source checkout chosen
  during installation. After a successful change, print
  `{"hookSpecificOutput":{"hookEventName":"SessionStart","reloadSkills":true}}`
  so Claude sees the skill on the first prompt. No change means no reload.
- `PostToolUseFailure`: optional Claude Code hint after a failed command, such
  as “read back the runner before retrying.” The command owns failure reporting
  and provider read-back.
- `PreToolUse`: optional guard for a named operation. Claude passes the
  proposed `tool_name` and `tool_input` before execution; a hook can return
  `permissionDecision: "deny"`. For example, it can deny a proposed
  `glab api ... -X POST` Bash call and name the CI Skills runner plan. For an
  access question, it can point to `gitlab_access.py check --json`, which
  reports the selected target and identity. An MCP tool
  needs its own matcher; command-text matching is fragile, so no broad guard
  is installed by default.
- `PermissionRequest`, `PermissionDenied`, `PostToolUse`: no CI Skills hook by
  default. The agent's permissions and the command's plan and result govern
  execution.

Claude user hooks belong in `~/.claude/settings.json`; project hooks belong in
`.claude/settings.json`. Codex also supports hooks at `~/.codex/hooks.json` or
`<repo>/.codex/hooks.json`, but its documented `SessionStart` output has no
`reloadSkills` field. Install Codex skills in `~/.codex/skills/ci-skills` or
`<repo>/.agents/skills/ci-skills` before the session.

**Benefit:** Claude can refresh a changed local skill for the same session.
An optional `PreToolUse` rule can steer a known direct API write to the
agent-facing command. **Cost:** each refresh adds startup work; command-text
matching can miss equivalent calls, and a missing source can leave a stale
skill. Keep refresh bounded and report failure; never retry a write from a
hook. Capability targets remain selected at command runtime, including from
`~/.ci-skills/target.toml`.

See the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
and [Codex hooks reference](https://learn.chatgpt.com/docs/hooks).
