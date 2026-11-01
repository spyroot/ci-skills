# OpenAI recommendation

Skill Scope Location Suggested use REPO $CWD/.agents/skills Current working directory: where you launch Codex. If you’re
in a repository or code environment, teams can check in skills relevant to a working folder. For example, skills only
relevant to a microservice or a module. REPO $CWD/../.agents/skills A folder above CWD when you launch Codex inside a
Git repository. If you’re in a repository with nested folders, organizations can check in skills relevant to a shared
area in a parent folder. REPO $REPO_ROOT/.agents/skills The topmost root folder when you launch Codex inside a Git
repository. If you’re in a repository with nested folders, organizations can check in skills relevant to everyone using
the repository. These serve as root skills available to any subfolder in the repository. USER $HOME/.agents/skills Any
skills checked into the user’s personal folder. Use to curate skills relevant to a user that apply to any repository the
user may work in. ADMIN /etc/codex/skills Any skills checked into the machine or container in a shared, system location.
Use for SDK scripts, automation, and for checking in default admin skills available to each user on the machine. SYSTEM
Bundled with Codex by OpenAI. Useful skills relevant to a broad audience such as the skill-creator and plan skills.
Available to everyone when they start Codex.

Codex supports symlinked skill folders and follows the symlink target when scanning these locations.

These locations are for authoring and local discovery. When you want to distribute reusable skills beyond a single repo,
or optionally bundle them with connectors, use plugins.
