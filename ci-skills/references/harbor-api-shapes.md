# Harbor API request shapes

ci-skills publishes a toolbox image to Harbor through `bin/harbor_push.py` and the chain's shared push callback,
and consumes it with `bin/harbor_pull.py`; the
[toolbox specification](../schemas/toolbox.schema.json) is the input it validates. Around that chain Harbor's
v2 API accepts the request shapes below. Each block lists every key the swagger declares for that request, with
the type, default and meaning the swagger and the request handlers state at Harbor v2.15.4. Values are examples;
server-assigned keys (`id`, `project_id`, `creator`, `status` and the timestamps) are left out of request blocks.

Requests go to `https://HARBOR/api/v2.0`, authenticated with HTTP basic: a user, or a robot's full login name
and secret. `project_name_or_id` is read as an id when it is all digits and as a name otherwise; the header
`X-Is-Resource-Name: true` forces the name reading. A `/` inside
`repository_name` is URL-encoded twice: `toolbox/runner` travels as `toolbox%252Frunner`. List endpoints share
`page` (default 1), `page_size` (default 10), `sort` (`field1,-field2`) and `q`
(`k=v` exact, `k=~v` fuzzy, `k=[min~max]` range, `k={v1 v2}` union, `k=(v1 v2)` intersection, comma-separated).

## Project create and update

`POST /projects` creates a project and returns `201` with its path in the `Location` header.
`PUT /projects/{project_name_or_id}` takes the same body and updates `metadata` and `cve_allowlist`; the storage
limit of an existing project changes through its [quota](#project-quotas).

```jsonc
{
  "project_name": "toolbox",              // string, at most 255 characters; required on create, fixed afterwards
  "public": false,                        // boolean; deprecated: copied into metadata.public when that is empty
  "storage_limit": 10737418240,           // int64 bytes; -1 unlimited; omitted = system default per project;
                                          // positive values at most 1125899906842624; administrators only
  "registry_id": null,                    // int64; proxy-cache project only: the registry to proxy, id > 0
  "cve_allowlist": {                      // project allowlist; ignored while reuse_sys_cve_allowlist is "true"
    "items": [{"cve_id": "CVE-2024-0001"}],   // cve_id string per entry
    "expires_at": null                    // integer seconds since epoch; omitted = the allowlist does not expire
  },
  "metadata": {                           // every value is a string
    "public": "false",                    // "true"|"false"; default "false"
    "enable_content_trust": "false",      // "true"|"false"; pulls of unsigned images are refused; not on proxy cache
    "enable_content_trust_cosign": "false", // "true"|"false"; pulls without a cosign signature are refused
    "prevent_vul": "false",               // "true"|"false"; pulls at or above severity are refused
    "severity": "critical",               // "none"|"low"|"medium"|"high"|"critical"; threshold for prevent_vul
    "auto_scan": "true",                  // "true"|"false"; scan each pushed artifact
    "auto_sbom_generation": "false",      // "true"|"false"; generate an SBOM for each pushed artifact
    "reuse_sys_cve_allowlist": "true",    // "true"|"false"; use the system allowlist instead of cve_allowlist
    "proxy_speed_kb": "-1",               // int32 as text; proxy-cache bandwidth to the upstream in Kbps; -1 unlimited
    "max_upstream_conn": "-1",            // int32 as text; proxy-cache connections per artifact; -1 unlimited
    "proxy_cache_local_on_not_found": "false" // "true"|"false"; proxy cache serves images removed upstream
  }
}
```

The handler rejects a `metadata.public` other than `"true"` or `"false"`, a `registry_id` at or below zero or of a
type not permitted for proxy caching, and a `metadata.retention_id` other than empty on create or other than the
project's current value on update: Harbor sets that key itself when a [retention policy](#retention-policies) is
created for the project. `proxy_speed_kb` and `max_upstream_conn` are
dropped on a project without `registry_id`; `enable_content_trust` is dropped on a proxy-cache project.

Metadata is also written one key at a time. `POST /projects/{project_name_or_id}/metadatas/` and
`PUT /projects/{project_name_or_id}/metadatas/{meta_name}` take a body with exactly one key, such as
`{"auto_scan": "true"}`; `GET /projects/{project_name_or_id}/metadatas/` returns every key as text, and
`DELETE .../metadatas/{meta_name}` removes one. `GET /projects/{project_name_or_id}/summary` returns repository and
member counts and the quota's `hard` and `used` storage.

## Robot accounts

`POST /robots` creates a robot and returns `201` with the secret, shown once:
`{"id": 7, "name": "robot$toolbox+pusher", "secret": "...", "creation_time": "...", "expires_at": 1735689600}`.
The login name is the configured prefix (`robot$` by default) plus `PROJECT+NAME` for a project robot or `NAME`
for a system robot.

```jsonc
{
  "name": "pusher",                       // string; ^[a-z0-9]+(?:[._-][a-z0-9]+)*$
  "description": "pushes the toolbox",    // string
  "secret": "",                           // string; ignored on create: the secret is generated and returned once
                                          // in the 201 body; set one later with PATCH, see below
  "level": "project",                     // "project"|"system"
  "disable": false,                       // boolean; default false; applied by PUT only, not on create
  "duration": 30,                         // int64 days; -1 for no expiry, otherwise a positive integer
  "permissions": [                        // required; a project robot holds exactly one entry
    {
      "kind": "project",                  // "project"|"system"
      "namespace": "toolbox",             // kind project: the project name, or "*" for every project (system
                                          // robot only); kind system: "/"
      "access": [                         // required; each pair is one of the rows below
        {"resource": "repository", "action": "pull", "effect": "allow"},   // effect "allow"|"deny"; default allow
        {"resource": "repository", "action": "push"}
      ]
    }
  ]
}
```

`GET /robots?q=Level=project,ProjectID=12` lists; `GET /robots/{robot_id}` reads; `PUT /robots/{robot_id}` takes the
same keys plus `id`, with `name` and `level` exactly as `GET` returns them (the full login name); it replaces
`permissions`, `description`, `disable` and `duration`;
`PATCH /robots/{robot_id}` with `{"secret": ""}` generates a new secret and with a supplied value sets it, when
that value has 8 to 128 characters with a lowercase letter, an uppercase letter and a digit;
`DELETE /robots/{robot_id}` removes the robot. `GET /permissions` returns the live `system` and `project` pair lists.
The rows marked provider in the tables are added by the API's robot permission provider on top of the policy map.

Project-kind pairs, one row per resource:

| resource | actions |
| --- | --- |
| `repository` | `read`, `update`, `delete`, `list`, `pull`, `push` |
| `artifact` | `read`, `create`, `list`, `delete` |
| `tag` | `create`, `list`, `delete` |
| `artifact-label` | `create`, `delete` |
| `artifact-addition` | `read` |
| `accessory` | `list` |
| `scan` | `create`, `read`, `stop` |
| `sbom` | `create`, `read`, `stop` |
| `scanner` | `create`, `read` |
| `project` | `read`, `update`, `delete` |
| `metadata` | `read`, `create`, `update`, `delete`, `list` |
| `label` | `read`, `create`, `update`, `delete`, `list` |
| `log` | `list` |
| `quota` | `read` |
| `tag-retention` | `read`, `create`, `update`, `delete`, `list` |
| `immutable-tag` | `create`, `update`, `delete`, `list` |
| `notification-policy` | `read`, `create`, `update`, `delete`, `list` |
| `preheat-policy` | `read`, `create`, `update`, `delete`, `list` |
| `robot` | `create`, `read`, `list`, `delete` (provider) |
| `member` | `create`, `read`, `update`, `list`, `delete` (provider) |
| `export-cve` | `create`, `read` (provider) |

System-kind pairs:

| resource | actions |
| --- | --- |
| `project` | `list`, `create` |
| `registry` | `read`, `create`, `update`, `delete`, `list` |
| `replication-policy` | `read`, `create`, `update`, `delete`, `list` |
| `replication` | `read`, `create`, `list` |
| `replication-adapter` | `list` |
| `scanner` | `read`, `create`, `update`, `delete`, `list` |
| `scan-all` | `read`, `create`, `update`, `stop` |
| `label` | `read`, `create`, `update`, `delete` |
| `quota` | `read`, `list` |
| `catalog` | `read` |
| `audit-log` | `list` |
| `purge-audit` | `read`, `create`, `update`, `list`, `stop` |
| `garbage-collection` | `read`, `create`, `update`, `list`, `stop` |
| `jobservice-monitor` | `list`, `stop` |
| `system-volumes` | `read` |
| `security-hub` | `read`, `list` |
| `preheat-instance` | `read`, `create`, `update`, `delete`, `list` |
| `robot` | `create`, `read`, `list`, `delete` (provider) |
| `user` | `create`, `read`, `update`, `list`, `delete` (provider) |
| `user-group` | `create`, `read`, `update`, `list`, `delete` (provider) |
| `ldap-user` | `create`, `list` (provider) |
| `quota` | `update` (provider; `read` and `list` above) |

## Retention policies

`POST /retentions` creates a policy, returns `201` with its id in the `Location` header, and sets the project's
`retention_id` metadata; a project holds one policy, so a second create is refused. `GET /retentions/{id}` reads
it, `PUT /retentions/{id}` replaces it with the same body plus `id`, and `DELETE /retentions/{id}` removes it and
clears the metadata key. `POST /retentions/{id}/executions` with `{"dry_run": true}` reports what a run would
delete; `{"dry_run": false}` deletes. `GET /retentions/metadatas` lists the templates and selectors the server
accepts.

```jsonc
{
  "algorithm": "or",                      // "or": an artifact stays when any rule retains it
  "scope": {"level": "project", "ref": 12},   // level "project"; ref is the project id
  "trigger": {
    "kind": "Schedule",                   // "Schedule"
    "settings": {"cron": "0 0 2 * * *"},  // six-field cron with seconds first; "" leaves manual runs only
    "references": {}                      // server-owned
  },
  "rules": [
    {
      "disabled": false,                  // boolean
      "priority": 0,                      // integer
      "action": "retain",                 // "retain"
      "template": "latestPushedK",        // one of the templates below
      "params": {"latestPushedK": 10},    // one key named after the template; integer; empty for "always"
      "tag_selectors": [
        {
          "kind": "doublestar",           // "doublestar"
          "decoration": "matches",        // "matches"|"excludes"
          "pattern": "**",                // doublestar glob; "{v*,release-*}" lists alternatives
          "extras": "{\"untagged\": true}"   // JSON text; untagged true also covers artifacts without a tag
        }
      ],
      "scope_selectors": {
        "repository": [                   // the only scope key
          {"kind": "doublestar", "decoration": "repoMatches", "pattern": "**"}   // "repoMatches"|"repoExcludes"
        ]
      }
    }
  ]
}
```

Templates and their single integer parameter: `latestPushedK` (count), `latestPulledN` (count), `latestActiveK`
(count), `lastXDays` (days), `nDaysSinceLastPush` (days), `nDaysSinceLastPull` (days) and `always` (no parameter).

## Immutable tag rules

`POST /projects/{project_name_or_id}/immutabletagrules` creates a rule and returns `201`;
`PUT .../immutabletagrules/{immutable_rule_id}` changes it or flips `disabled`; `DELETE` removes it. A tag matched
by an enabled rule cannot be overwritten or deleted, and artifact listings with `with_immutable_status=true` show
`tags[].immutable`.

```jsonc
{
  "disabled": false,                      // boolean
  "priority": 0,                          // integer
  "action": "immutable",                  // "immutable"
  "template": "immutable_template",       // "immutable_template"
  "params": {},                           // object; immutable_template takes no parameter
  "tag_selectors": [
    {"kind": "doublestar", "decoration": "matches", "pattern": "v*"}      // "matches"|"excludes"
  ],
  "scope_selectors": {
    "repository": [
      {"kind": "doublestar", "decoration": "repoMatches", "pattern": "**"}   // "repoMatches"|"repoExcludes"
    ]
  }
}
```

## Project quotas

`GET /quotas?reference=project&reference_id=12` returns the project's quota; `GET /quotas/{id}` reads one.
`PUT /quotas/{id}` sets the hard limit; the body is required and `storage` is the only resource.

```jsonc
{
  "hard": {
    "storage": 21474836480               // int64 bytes; -1 unlimited; positive values at most 1125899906842624
  }
}
```

A quota reads back as `{"id": 3, "ref": {"id": 12, "name": "toolbox", "owner_name": "admin"},
"hard": {"storage": 21474836480}, "used": {"storage": 734003200}, "creation_time": "...", "update_time": "..."}`.
A push that would exceed `hard.storage` is denied with the message "Quota exceeded when processing the request";
the matching [webhook](#webhook-policies) event is `QUOTA_EXCEED`.

## Registries

A registry is a remote endpoint used by replication policies and proxy-cache projects. `POST /registries` creates one
and returns `201`; `GET /registries/{id}` reads it; `DELETE /registries/{id}` removes it.

```jsonc
{
  "name": "upstream-quay",                // string
  "type": "quay",                         // adapter: "harbor", "docker-hub", "docker-registry", "quay", "gitlab",
                                          // "github-ghcr", "aws-ecr", "azure-acr", "google-gcr", "ali-acr",
                                          // "huawei-SWR", "tencent-tcr", "volcengine-cr", "jfrog-artifactory", "dtr"
  "url": "https://quay.io",               // string
  "description": "",                      // string
  "insecure": false,                      // boolean; true skips certificate verification
  "ca_certificate": "",                   // PEM text; verifies this endpoint instead of the system CA pool
  "credential": {
    "type": "basic",                      // "basic"|"oauth"; "secret" is Harbor's own internal type
    "access_key": "robot-name",           // user name for basic
    "access_secret": ""                   // password or token
  }
}
```

`POST /registries/ping` checks reachability with either `{"id": 3}` or the flat keys `type`, `url`,
`credential_type`, `access_key`, `access_secret`, `insecure` and `ca_certificate`. `PUT /registries/{id}` takes
`name`, `description`, `url`, `credential_type`, `access_key`, `access_secret`, `insecure` and `ca_certificate`; an
omitted key keeps its value. `GET /replication/adapters` lists the
adapter types the server has; `GET /registries/{id}/info` lists the filters and triggers one registry supports.

## Replication policies

`POST /replication/policies` creates a policy and returns `201`; `PUT /replication/policies/{id}` replaces it;
`GET` and `DELETE` read and remove it. One side is this Harbor: a remote `dest_registry` makes a push policy and a
remote `src_registry` a pull policy. `POST /replication/executions` with `{"policy_id": 7}` starts a run and
`GET /replication/executions?policy_id=7` lists runs with `status`, `total`, `succeed`, `failed` and `in_progress`.

```jsonc
{
  "name": "mirror-toolbox",               // string
  "description": "",                      // string
  "src_registry": {"id": 0},              // registry id; 0 or omitted is this Harbor
  "dest_registry": {"id": 3},             // registry id; 0 or omitted is this Harbor
  "dest_namespace": "mirror",             // destination project or namespace; "" keeps the source namespace
  "dest_namespace_replace_count": -1,     // int8; leading path components replaced by dest_namespace;
                                          // -1 legacy mode, 0 keeps every level
  "trigger": {
    "type": "manual",                     // "manual"|"scheduled"|"event_based"
    "trigger_settings": {"cron": ""}      // six-field cron for scheduled
  },
  "filters": [
    {"type": "name", "value": "toolbox/**"},                       // repository doublestar; no decoration
    {"type": "tag", "value": "v*", "decoration": "matches"},       // "matches"|"excludes"
    {"type": "label", "value": ["prod"], "decoration": "matches"}, // array of label names
    {"type": "resource", "value": "image"}                         // "image"|"artifact"; no decoration
  ],
  "replicate_deletion": false,            // boolean; "deletion" is the deprecated alias
  "override": true,                       // boolean; overwrite artifacts already at the destination
  "enabled": true,                        // boolean
  "speed": -1,                            // int32 KB/s per task; -1 unlimited
  "copy_by_chunk": false,                 // boolean; chunked blob copy where the registry supports it
  "single_active_replication": false      // boolean; skip a run while the previous one is active
}
```

## Webhook policies

`POST /projects/{project_name_or_id}/webhook/policies` creates a policy and returns `201`; `targets` and
`event_types` are required. `PUT .../webhook/policies/{webhook_policy_id}` takes every key except `id`,
`project_id`, `creation_time` and `update_time`; `GET` and `DELETE` read and remove one.
`GET /projects/{project_name_or_id}/webhook/events` returns the live `event_type`, `notify_type` and
`payload_formats` lists.

```jsonc
{
  "name": "notify-ci",                    // string
  "description": "",                      // string
  "enabled": true,                        // boolean
  "event_types": ["PUSH_ARTIFACT"],       // "PUSH_ARTIFACT", "PULL_ARTIFACT", "DELETE_ARTIFACT", "QUOTA_EXCEED",
                                          // "QUOTA_WARNING", "SCANNING_COMPLETED", "SCANNING_FAILED",
                                          // "SCANNING_STOPPED", "REPLICATION", "TAG_RETENTION"
  "targets": [
    {
      "type": "http",                     // "http"|"slack"
      "address": "https://ci.example.internal/hooks/harbor",   // endpoint URL
      "auth_header": "",                  // sent as the Authorization header value
      "skip_cert_verify": false,          // boolean
      "payload_format": "Default"         // "Default"|"CloudEvents" for http
    }
  ]
}
```

## Artifacts and scan overview

`GET /projects/{project_name}/repositories/{repository_name}/artifacts` lists artifacts;
`GET .../artifacts/{reference}` reads one by tag or digest with the same `with_*` switches.

```text
q=tags=~v1,size=[1000000~]                    # string; query patterns as above; keys are artifact fields;
                                              # time values are written "YYYY-MM-DD HH:MM:SS"
sort=-push_time                               # string; "-" descends
page=1 page_size=10                           # integers; defaults 1 and 10
with_tag=true                                 # boolean; default true
with_label=false                              # boolean; default false
with_scan_overview=false                      # boolean; default false; adds scan_overview
with_sbom_overview=false                      # boolean; default false; adds sbom_overview
with_immutable_status=false                   # boolean; default false; adds tags[].immutable
with_accessory=false                          # boolean; default false; adds accessories
with_signature=false                          # boolean; default false; single-artifact read only
X-Accept-Vulnerabilities: application/vnd.security.vulnerability.report; version=1.1, application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0
                                              # header; comma-separated report MIME types; the default above; the first one found keys scan_overview
```

```jsonc
[
  {
    "id": 101,                            // int64
    "type": "IMAGE",                      // artifact type
    "media_type": "application/vnd.oci.image.config.v1+json",
    "manifest_media_type": "application/vnd.oci.image.manifest.v1+json",
    "artifact_type": "application/vnd.oci.image.config.v1+json",   // artifactType from the manifest
    "project_id": 12, "repository_id": 34, "repository_name": "toolbox/runner",
    "digest": "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "size": 123456789,                    // int64 bytes
    "push_time": "...", "pull_time": "...",   // RFC 3339 timestamps
    "extra_attrs": {},                    // config fields such as architecture, os, created
    "annotations": {},                    // manifest annotations
    "references": [],                     // children of an index: child_digest, platform
    "tags": [
      {"id": 5, "name": "v1", "push_time": "...", "pull_time": "...", "immutable": false,
       "artifact_id": 101, "repository_id": 34}
    ],
    "labels": [],                         // id, name, color, scope, project_id
    "scan_overview": {                    // keyed by report MIME type
      "application/vnd.security.vulnerability.report; version=1.1": {
        "report_id": "5f62c830-f996-11e9-957f-0242c0a89008",
        "scan_status": "Success",         // "Pending"|"Scheduled"|"Running"|"Success"|"Error"|"Stopped"
        "severity": "Critical",           // overall: "None"|"Unknown"|"Negligible"|"Low"|"Medium"|"High"|"Critical"
        "duration": 42,                   // seconds
        "complete_percent": 100,          // 0 to 100
        "start_time": "...", "end_time": "...",
        "summary": {
          "total": 12, "fixable": 9,      // counts
          "summary": {"Critical": 1, "High": 4, "Medium": 5, "Low": 2}   // count per severity
        },
        "scanner": {"name": "Trivy", "vendor": "Aqua Security", "version": "v0.9.1"}
      }
    },
    "sbom_overview": {},                  // start_time, end_time, scan_status, sbom_digest, report_id, duration, scanner
    "accessories": [],                    // signatures and SBOMs: type, digest, size, subject_artifact_digest
    "addition_links": {                   // href, absolute
      "vulnerabilities": {"href": "/api/v2.0/projects/toolbox/repositories/runner/artifacts/sha256:9f86.../additions/vulnerabilities", "absolute": false}
    }
  }
]
```

`POST .../artifacts/{reference}/scan` with `{"scan_type": "vulnerability"}` or `{"scan_type": "sbom"}` starts a scan
and `POST .../scan/stop` with the same body stops it. `GET .../artifacts/{reference}/additions/vulnerabilities`
returns the full report. `POST .../artifacts/{reference}/tags` with `{"name": "v2"}` adds a tag;
`DELETE .../artifacts/{reference}` removes the artifact; `POST .../artifacts?from=project/repository:tag` copies one in.
`GET /projects/{project_name}/repositories` lists repositories with `artifact_count` and `pull_count`.

## Sources

Harbor v2.15.4: `api/v2.0/swagger.yaml` (shapes, parameters, defaults); `src/common/rbac/const.go` (robot
access pairs); `src/server/v2.0/handler/project.go`, `project_metadata.go`, `robot.go`, `quota.go` and `artifact.go`
and `src/lib/quota_storage_limit.go` (validation); `src/controller/robot/controller.go` and `model.go` (login
name, scopes); `src/pkg/retention/policy/models.go` and `rule/index/index.go` (templates);
`src/lib/selector/selectors/doublestar/selector.go` (decorations); `src/pkg/reg/model/policy.go`, `registry.go`
and `resource.go` (filters, triggers, adapters); `src/pkg/notification/notification.go`,
`src/controller/event/topic.go` and `src/pkg/notifier/formats` (webhook values); `src/pkg/scan/rest/v1/spec.go`,
`src/pkg/scan/vuln/severity.go` and `src/jobservice/job/status.go` (report words).
