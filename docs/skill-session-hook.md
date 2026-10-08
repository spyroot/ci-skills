# Agent hooks for CI Skills

An agent lifecycle hook is registered in Codex or Claude settings and runs on
their tool events. `PreToolUse` sees a proposed tool call; `PostToolUse` sees
its result. `statusMessage` labels the brief hook run in the UI. Git invokes
pre-commit hooks during `git commit`; they are unrelated to this design. The
CI Skills command performs the work and reports its result.

## Delivery

The installer does not register these hooks today. The next delivery adds one
adapter beside the installed skill. A default Codex install registers it in
`~/.codex/config.toml`; an install into `~/.claude/skills` registers it in
`~/.claude/settings.json`. Both point to the installed skill path.
The adapter recognizes CI Skills calls, reuses their catalog and target
resolver, and gives the agent a missing-prerequisite or result hint. It does
not repeat provider calls, rewrite commands, or retry writes. This delivery
does not change `bless.sh`, `Makefile`, `scripts/check.sh`, or Git hooks.

For example, the installer could write this Codex configuration after
substituting the installed skill path and its Python 3.11+ runtime. This
adapter does not exist today:

```toml
[[hooks.PreToolUse]]
matcher = "^Bash$"
[[hooks.PreToolUse.hooks]]
type = "command"
command = '"/path/to/python-3.11" "/installed/ci-skills/bin/agent_hook.py" pre'
statusMessage = "Checking tool call"

[[hooks.PostToolUse]]
matcher = "^Bash$"
[[hooks.PostToolUse.hooks]]
type = "command"
command = '"/path/to/python-3.11" "/installed/ci-skills/bin/agent_hook.py" post'
statusMessage = "Reviewing tool output"
```

`Bash` matches every shell call, so these neutral labels may briefly appear
for unrelated commands even when the adapter exits immediately. A
capability-specific hook label needs a named tool matcher; the CI Skills
command shows progress while gathering a cluster-health report. Claude uses
its own hook settings and `PostToolUseFailure` for failed tool calls. Its
`SessionStart` can request `reloadSkills` after a skill update; Codex does not
document that output field.

Proof for this delivery: in each agent run the installed
`gitlab_access.py check --json` against the selected target; observe the hook
label and a `PASS` report naming that target and identity. An unrelated shell
call must proceed without CI Skills work. References: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude Code hooks](https://code.claude.com/docs/en/hooks).
