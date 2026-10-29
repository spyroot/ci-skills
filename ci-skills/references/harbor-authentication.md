# Harbor authentication inputs

## Select credentials

The `[harbor]` section of `~/.ci-skills/target.toml` selects the origin, project
and existing credential files. Relative paths resolve beside the selected
target file. Other providers are needed only when the selected command uses them.

```toml
[harbor]
url = "https://registry.example.com"
project = "builds"
username_file = "credentials/harbor.username"
password_file = "credentials/harbor.password"
```

Use both file fields above, or replace them with `credential_file`; do not mix
the two forms. The combined file accepts `username:password`, an object with
`username`/`password`, or Docker `auths` keyed by the selected host/HTTPS origin.
Docker entries accept `auth` or `username`/`password`; credential helpers are
not resolved. A Harbor robot export containing `name`/`secret` is not this
combined-file format: select its account name and secret through paired files.

## Account choices

| Harbor identity | Registry username / password |
| --- | --- |
| Database user | Harbor username / password. |
| LDAP/AD user | Directory username / password. |
| OIDC user | Harbor username / Harbor CLI secret. |
| Project robot | Exact configured prefix + project + `+` + account / robot secret. |
| System robot | Exact configured prefix + account / robot secret. |

OIDC CLI clients use the CLI secret from the user's Harbor profile; a browser
login or identity-provider password does not supply that secret. Replacing the
CLI secret invalidates the previous one. Sources: Harbor 2.15
[authentication][authentication] and [OIDC CLI authentication][oidc].

## Robot scope and expiry

Project robots act within one project. System robots can receive permissions
across selected projects. Use the full returned username; the prefix is
configurable. Robot accounts authenticate OCI/API requests, not web UI sessions.
For pushes, Harbor pairs Push Repository with Pull Repository permission.
Expiry, disabled state, selected projects and permissions determine whether
the requested action can succeed. Creation/refresh yields a secret that must be
retained; an existing secret cannot later be retrieved from Harbor.
Sources: Harbor 2.15 [project robots][project-robots] and [system robots][system-robots].

## Publish an image with ci-skills

`harbor_push.py` copies a registry image into the configured project and verifies
its digest. `REPOSITORY` is within that project. Use the installed environment
from [SKILL.md](../SKILL.md); replace uppercase values with caller selections.

```bash
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/harbor_push.py" push \
  --source-image SOURCE_IMAGE --repository REPOSITORY --tag TAG --json
conda run -n ci-skills python "$CI_SKILLS_ROOT/bin/harbor_push.py" push \
  --source-image SOURCE_IMAGE --repository REPOSITORY --tag TAG \
  --apply --confirm-plan PLAN_DIGEST --json
```

The first invocation plans; use its `plan_digest` for the second. This wrapper
does not create robot accounts or change the Harbor authentication mode.

## Related

- [Helm OCI](harbor-helm-oci.md): chart addressing and native Helm commands.
- `harbor_pull.py`: run the published toolbox doctor in a temporary cluster Job.
- [Access](access.md): target and credential selection.

Sources checked 2026-10-18; the deployed Harbor version may differ.

[authentication]: https://goharbor.io/docs/2.15.0/administration/configure-authentication/
[oidc]: https://goharbor.io/docs/2.15.0/administration/configure-authentication/oidc-auth/
[project-robots]: https://goharbor.io/docs/2.15.0/working-with-projects/project-configuration/create-robot-accounts/
[system-robots]: https://goharbor.io/docs/2.15.0/administration/robot-accounts/
