# GitLab runner configuration and job tuning

`gitlab_runner.py` creates, tags, assigns and deletes the runner record on GitLab and saves the one-time
token; `gitlab_job.py` and `gitlab_pipeline.py` read what the runner did with a job. The runner process itself
is driven by `config.toml` on the host, Pod or Operator that runs it, and by job variables in `.gitlab-ci.yml`.
Each section below is one table of that file or one task, with the most important keys the runner's own
configuration types accept, each with a trailing comment in the form `type | default | effect`. Values are
examples. `unset` means the key has no value until you set one.

## Global section

Top of `config.toml`, outside every `[[runners]]` entry. These apply to the whole runner process.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
concurrent = 10                  # int | 1 | jobs running at once across every [[runners]] entry; caps their total even when the limits add up to more; 0 exits with a critical error
check_interval = 3               # int seconds | 3 | job request loop period, divided across the [[runners]] entries; 0 or lower restores 3
log_level = "info"               # string | info | panic, fatal, error, warn, info or debug; a --log-level command flag wins
log_format = "runner"            # string | runner | runner (ANSI color), text or json; a --log-format command flag wins
log_field_schema_version = 0     # int | 0 | log fields standardized at or before this version use their standardized names; 0 keeps legacy names
log_field_dual_emit_target = 0   # int | 0 | also emit standardized names for fields standardized up to this version; 0 off; must exceed the schema version
listen_address = "[::]:9252"     # string | unset | host:port of the Prometheus metrics HTTP server; unset starts no server
connection_max_age = "15m"       # duration | 15m | lifetime of a TLS keepalive connection to GitLab; 0 or lower keeps it as long as possible
shutdown_timeout = 30            # int seconds | 30 | deadline of the forceful shutdown; 0 or lower restores 30; raise it when [machine.shutdown_drain] is on
sentry_dsn = ""                  # string | SENTRY_DSN environment variable | report system-level errors to Sentry; the file value wins and is not expanded
user = ""                        # string | unset | run shell-executor job scripts as this host user
[labels]                         # table of string = string | unset | default custom worker labels; a [[runners]] labels table overrides keys
  team = "platform"
```

### Session server

One `[session_server]` table at the root enables the interactive web terminal and session proxy for every
runner. Delete the table to disable both. A running process needs `gitlab-runner restart` to pick up changes.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[session_server]
  listen_address = "[::]:8093"                  # string | unset | internal host:port the session server binds; the runner mints a TLS certificate for it
  advertise_address = "runner.example.com:8093" # string | listen_address | host:port GitLab connects to; public unless local web hook requests are allowed
  session_timeout = 1800                        # int seconds | 1800 | how long a session stays open after the job ends; the job waits for it
```

### Docker Machine global section

`[machine]` applies to every `docker+machine` runner. `shutdown_drain` removes idle machines while the runner
process stops, which otherwise is left to an external hook; the buckets shape the machine-creation histograms.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[machine]
  creation_duration_seconds_buckets = [30, 37.5, 46.9]     # array of floats | 30 growing by 1.25 for 14 buckets | buckets of gitlab_runner_autoscaling_machine_creation_duration_seconds; read at start
  creation_phase_duration_seconds_buckets = [1, 2, 5, 10]   # array of floats | 1, 2, 5, 10, 20, 40, 80, 160, 320, 640 | per-phase buckets; need FF_DOCKER_MACHINE_JSON_OUTPUT and Docker Machine 0.16.2-gitlab.55+
  disable_creation_phase_metrics = false                    # bool | false | stop recording the phase histogram; phase durations are still logged
  [machine.shutdown_drain]
    enabled = true          # bool | false | remove idle machines during shutdown; uses the global shutdown_timeout, which the page's example raises to 600
    early = false           # bool | false | start removing idle machines when job requests stop instead of after the last job finishes
    concurrency = 3         # int | 3 | machines removed at once
    max_retries = 3         # int | 3 | removal attempts per machine
    retry_backoff = "5s"    # duration | 5s | base wait between attempts, multiplied by the attempt number
  [machine.heartbeat]
    concurrency = 5         # int | 5 | concurrent liveness-label refreshes across all runners; see HeartbeatInterval under Docker Machine autoscaling
```

## Runners section

One `[[runners]]` entry per registered runner. `url` and `token` accept `$VAR` expansion so secrets can stay in
the environment. Multi-line scripts use a triple-quoted string.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[[runners]]
  name = "example-runner"                  # string | unset | description shown in logs; informational
  url = "https://gitlab.example.com/"      # string | unset | GitLab instance URL; expands $GITLAB_URL
  api_url = ""                             # string | url | URL for job requests, updates, log patches and token checks; not for clones or artifacts
  job_router_url = ""                      # string | unset | experiment; Job Router URL used when FF_USE_JOB_ROUTER is on, such as grpcs://kas.example.com:8150
  token = "$RUNNER_TOKEN"                  # string | unset | runner authentication token from registration, not the registration token
  id = 1234                                # int | written by registration | runner ID on GitLab
  token_obtained_at = 2030-01-01T00:00:00Z # datetime | written by registration | when the token was obtained
  token_expires_at = 2030-01-22T00:00:00Z  # datetime | written by registration | token expiry; rotation happens before it unless FF_DISABLE_AUTOMATIC_TOKEN_ROTATION
  tls-ca-file = ""                         # string | unset | CA bundle that verifies the GitLab peer over HTTPS
  tls-cert-file = ""                       # string | unset | client certificate for TLS authentication
  tls-key-file = ""                        # string | unset | private key of that client certificate
  limit = 0                                # int | 0 | jobs this entry handles at once; 0 is unlimited; the global concurrent still caps the total
  executor = "kubernetes"                  # string | unset | shell, docker, docker-windows, docker+machine, docker-autoscaler, instance, kubernetes, ssh, custom, parallels, virtualbox
  shell = "bash"                           # string | bash on Unix with sh as fallback; pwsh on Windows for the shell executor and new registrations; powershell on Windows for kubernetes and docker-windows | bash, sh, pwsh, powershell or legacy cmd for the generated job script
  builds_dir = ""                          # string | executor default | absolute directory for builds in the executor's context; $CI_BUILDS_DIR for jobs
  cache_dir = ""                           # string | executor default | absolute directory for local caches; docker needs it in volumes
  environment = ["ENV=value"]              # array of strings | unset | KEY=value pairs appended to or overwriting the job environment; FF_NAME=1 sets a feature flag
  request_concurrency = 1                  # int | 1 | parallel job requests from this entry; a maximum under FF_USE_ADAPTIVE_REQUEST_CONCURRENCY
  strict_check_interval = false            # bool | false | true polls once per check_interval even after receiving a job; evens distribution, longer queue wait
  output_limit = 4096                      # int kilobytes | 4096 | maximum job log size
  pre_get_sources_script = ""              # string | unset | commands run before the repository and submodules update; set Git client options here
  post_get_sources_script = ""             # string | unset | commands run after the repository and submodules update
  pre_build_script = ""                    # string | unset | runs in the before_script and script shell context before them; a failure skips the rest, after_script still runs
  post_build_script = ""                   # string | unset | runs in that same context after script; skipped when any of them failed
  clone_url = ""                           # string | url | URL used for git clone and fetch when the runner cannot reach url; also rewrites LFS endpoints
  debug_trace_disabled = false             # bool | false | true keeps CI_DEBUG_TRACE off for every job
  clean_git_config = true                  # bool | true, except shell executor or GIT_STRATEGY none | clean .git/config and the global config before and after the job
  use_git_packfile_uris = false            # bool | false | set fetch.uriProtocols=https so Git downloads server-offered packfile URIs; needs server and client support
  safe_directory_checkout = true           # bool | runner default | add a safe.directory entry for the job working directory to the global Git config
  unhealthy_requests_limit = 3             # int | 3 | 403 job-request responses in a row after which this worker stops requesting; running jobs continue
  unhealthy_interval = "1h"                # duration | 1h | longest disable; the first lasts 30 seconds and grows with jitter; 0 never disables
  job_status_final_update_retry_limit = 10 # int | 10 | retries of the final job status push to GitLab
  prepare_timeout = "10m"                  # duration | job timeout | deadline for executor start plus shell setup; unset, 0 or above the job timeout means the job timeout
  get_sources_timeout = "15m"              # duration | job timeout | deadline for clone or fetch including submodules; same fallback
  step_runner_image = ""                   # string | bundled | override the step-runner image injected for FF_SCRIPT_TO_STEP_MIGRATION and FF_CONCRETE
  proxy_exec = false                       # bool | unset | experiment; proxy command execution through the helper binary
  use_slot_cgroups = false                 # bool | false | slot-derived cgroup names per concurrent job for resource isolation (docker-autoscaler)
  slot_cgroup_template = ""                # string | unset | template for those names with a ${slot} placeholder
  [runners.allowed_docker_credential_helpers]   # table of string = array | unset (no helper runs) | helper name or path to the registry globs it may serve for a job's DOCKER_AUTH_CONFIG; docker and kubernetes
    ecr-login = ["*.dkr.ecr.*.amazonaws.com"]
  [runners.monitoring]                     # table | unset | experiment; monitoring settings of this runner, from the configuration types
  [runners.feature_flags]                  # table of FF_NAME = bool | page defaults | pinned flags that jobs cannot override; see Feature flags
    FF_SCRIPT_SECTIONS = true
```

### Artifact upload, build directory, referees and startup canary

Four small tables nested under one `[[runners]]` entry.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[[runners]]
  executor = "kubernetes"
  [runners.artifact]
    upload_timeout = "2h"                 # duration | 1h | whole artifact upload deadline; raise for large artifacts on slow links, lower to fail fast
    response_header_timeout = "15m"       # duration | 10m | wait for response headers after the upload body is sent
  [runners.custom_build_dir]
    enabled = true                        # bool | true for docker, kubernetes, docker+machine, docker-autoscaler and instance; false elsewhere | let jobs set GIT_CLONE_PATH under builds_dir
  [runners.referees]                      # docker+machine only; results upload as job artifacts
    [runners.referees.metrics]
      prometheus_address = "http://prometheus.example.internal:9090"   # string | unset | Prometheus server reachable from the runner manager when the job ends
      query_interval = 10                 # int seconds | unset | sampling interval of the time series
      queries = [                         # array of strings | unset | canonical_name:PromQL; {selector} is the runner instance label pair, {interval} the query_interval; the page example's metric_queries is not the key the referees type reads
        "cpu_seconds:rate(node_cpu_seconds_total{{selector}}[{interval}])",
      ]
  [runners.experimental.boot_verify]      # runs a synthetic job at process start; /health/ready waits for it and a failure exits non-zero
    enabled = true                        # bool | false | run the canary; docker and kubernetes need a default image
    timeout = "5m"                        # duration | 5m | canary deadline
    acquire_min_backoff = "1s"            # duration | 1s | minimum wait between executor acquire retries
    acquire_max_backoff = "10s"           # duration | 10s | maximum wait between those retries
    verify_auth = true                    # bool | false | check from the manager that the token authenticates before the synthetic job runs
    verify_server_url = true              # bool | false | probe the Git HTTP endpoint under clone_url, then url, from the job environment; any HTTP response passes
    verify_server_url_insecure = false    # bool | false | skip TLS verification in that probe
    verify_cache = true                   # bool | false | write and read back one object in the configured cache; skipped without a cache; leaves boot-verify/<short token>/ objects
    verify_commands = ["nvidia-smi"]      # array of strings | empty | commands appended to the canary script; the first failure fails the canary
    verify_helper_version = true          # bool | false | fail when the helper binary version differs from the runner's
```

## Docker executor

`[runners.docker]` applies when `executor` is `docker`, `docker-windows` or `docker+machine`. Docker-in-Docker
services and runtimes configured inside a job do not inherit these keys. Resource strings use Docker syntax such
as `"128m"`; `allowed_*` lists take wildcards and default to everything.

### Docker container settings

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.docker]
  host = ""                                # string | DOCKER_HOST, then unix:///var/run/docker.sock | Docker endpoint
  tls_cert_path = ""                       # string | unset | directory with ca.pem, cert.pem and key.pem for a TLS connection to the daemon
  tls_verify = false                       # bool | false | verify TLS to the daemon; needs the daemon on a TCP port
  image = "ruby:3.3"                       # string | unset | image for jobs that name none
  helper_image = ""                        # string | registry.gitlab.com/gitlab-org/gitlab-runner/gitlab-runner-helper:<arch>-<revision> | clone and artifact helper image; mirror it with ${CI_RUNNER_VERSION} tags
  helper_image_flavor = "alpine"           # string | alpine | alpine, alpine3.21, alpine-latest, ubi-fips or ubuntu
  helper_image_autoset_arch_and_os = false # bool | false | pick the helper architecture and OS from the host
  pull_policy = ["always", "if-not-present"]   # string or array | always | never, if-not-present, always; a list is tried in order; repeat always to retry a pull
  allowed_pull_policies = ["always", "if-not-present"]   # array | the pull_policy values | pull policies a job may request
  hostname = ""                            # string | unset | container hostname
  user = ""                                # string | image default | run every command as this user
  allowed_users = []                       # array of strings | unset | users a job may select for the build container
  group_add = ["docker"]                   # array of strings | unset | extra groups for the container process
  userns_mode = ""                         # string | unset | user namespace mode when daemon remapping is on, such as host
  privileged = false                       # bool | false | privileged container; insecure; capabilities keys then have no effect
  cap_add = ["NET_ADMIN"]                  # array of strings | unset | Linux capabilities added to the job container
  cap_drop = ["DAC_OVERRIDE"]              # array of strings | unset | capabilities dropped from the job container
  security_opt = ["seccomp=unconfined"]    # array of strings | unset | --security-opt key:value pairs; systempaths is not supported
  devices = ["/dev/net/tun"]               # array of strings | unset | host devices shared with the job container
  device_cgroup_rules = ["c 189:* rmw"]    # array of strings | unset | custom device cgroup rules
  gpus = "all"                             # string | unset | GPU request in docker CLI syntax; needs GPU-enabled daemon configuration
  runtime = ""                             # string | daemon default | container runtime such as nvidia or sysbox-runc
  isolation = ""                           # string | unset | Windows only: default, hyperv or process
  cgroup_parent = ""                       # string | unset | cgroup parent of the job container
  cpus = "2"                               # string | unset | number of CPUs
  cpu_shares = 1024                        # int | 1024 | relative CPU weight
  cpuset_cpus = "0,1"                      # string | unset | cgroup CpusetCpus
  cpuset_mems = "0,1"                      # string | unset | cgroup CpusetMems
  memory = "1g"                            # string | unset | memory limit; b, k, m or g; at least 4M
  memory_swap = "2g"                       # string | unset | memory plus swap limit
  memory_reservation = "512m"              # string | unset | memory soft limit
  oom_kill_disable = false                 # bool | false | keep processes alive on out-of-memory
  oom_score_adjust = 0                     # int | 0 | positive values are killed earlier
  shm_size = 300000                        # int bytes | unset | /dev/shm size
  ulimit = { nofile = "65536:65536" }      # table of string = string | unset | --ulimit values
  sysctls = { "net.ipv4.ip_forward" = "1" }   # table of string = string | unset | sysctl options
  tmpfs = { "/scratch" = "rw,size=1g" }    # table of string = mount options | unset | tmpfs mounts in the job container
  dns = ["192.0.2.53"]                     # array of strings | unset | DNS servers; invalid addresses fail the prepare stage
  dns_search = ["example.internal"]        # array of strings | unset | DNS search domains
  extra_hosts = ["registry:192.0.2.10"]    # array of strings | unset | /etc/hosts entries
  network_mode = ""                        # string | unset | attach to this network; FF_NETWORK_PER_BUILD creates one per job instead
  network_mtu = 0                          # int | unset | MTU of the per-job network created by FF_NETWORK_PER_BUILD
  enable_ipv6 = false                      # bool | false | IPv6 on per-job networks created by FF_NETWORK_PER_BUILD
  mac_address = "92:d0:c6:0a:29:33"        # string | unset | container MAC address; invalid values fail the prepare stage
  pid_mode = ""                            # string | unset | PID namespace such as host
  ipcmode = ""                             # string | unset | IPC namespace such as host or shareable
  links = ["mysql_container:mysql"]        # array of strings | unset | legacy container links into the job container
  log_options = { env = "GITLAB_CI_JOB_ID", labels = "com.gitlab.gitlab-runner.type" }   # table | unset | json-file log driver options; only env and labels
  container_labels = { team = "platform" } # table of string = string | unset | labels on every created container; values expand environment variables
  disable_entrypoint_overwrite = false     # bool | false | keep the image entrypoint instead of the runner's
  disable_cache = false                    # bool | false | skip the automatically created cache volumes; distributed cache unaffected
  cache_dir = ""                           # string | unset | where docker cache volumes live; absolute or relative to the working directory
  volumes = ["/cache", "/var/run/docker.sock:/var/run/docker.sock"]   # array of strings | unset | docker -v syntax; include cache_dir
  volumes_from = ["storage_container:ro"]  # array of strings | unset | inherit volumes from another container, :ro or :rw
  volume_driver = ""                       # string | unset | volume driver for the job container
  volume_driver_ops = { size = "50G" }     # table of string = string | unset | options for that driver
  volume_keep = false                      # bool | false | keep volumes after the job; the operator prunes them; avoids daemon stalls at high concurrency
  wait_for_services_timeout = 30           # int seconds | 30 | wait for services to start; -1 disables the wait
```

### Docker services, allowlists and service limits

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.docker]
  allowed_images = ["ruby:*", "python:*"]           # array of wildcards | everything | images a job may name; docker and kubernetes
  allowed_privileged_images = ["docker:*dind"]      # array of wildcards | everything | subset of allowed_images that runs privileged when privileged is on
  allowed_services = ["postgres:*", "redis:*"]      # array of wildcards | everything | services a job may name; docker and kubernetes
  allowed_privileged_services = ["docker:*dind"]    # array of wildcards | everything | subset that runs privileged when privileged or services_privileged is on
  services_privileged = false                       # bool | privileged | privileged mode for service containers only; insecure
  services_limit = -1                               # int | -1 | maximum services per job; -1 unlimited
  services_cap_add = ["NET_ADMIN"]                  # array of strings | docker default set | capabilities added to service containers
  services_cap_drop = ["ALL"]                       # array of strings | docker default set | capabilities dropped from service containers
  services_security_opt = ["seccomp=unconfined"]    # array of strings | unset | --security-opt for service containers
  services_devices = { "docker:*" = ["/dev/fuse"] } # table of image wildcard = device array | unset | host devices exposed to services by image name
  services_tmpfs = { "/var/lib/mysql" = "rw" }      # table of path = mount options | unset | tmpfs mounts in every service container
  service_memory = "512m"                           # string | unset | service memory limit
  service_memory_swap = "1g"                        # string | unset | service memory plus swap limit
  service_memory_reservation = "256m"               # string | unset | service memory soft limit
  service_cpus = "1"                                # string | unset | CPUs per service
  service_cpu_shares = 1024                         # int | 1024 | service relative CPU weight
  service_cpuset_cpus = "2,3"                       # string | unset | service cgroup CpusetCpus
  service_gpus = ""                                 # string | unset | GPU request for services in docker CLI syntax
  service_cgroup_parent = ""                        # string | unset | cgroup parent of service containers
  service_slot_cgroup_template = ""                 # string | unset | slot-derived cgroup names for services with a ${slot} placeholder
  [[runners.docker.services]]                       # one table per service started for every job, linked to the job container
    name = "registry.example.com/svc1"              # string | required | image of the service
    alias = "svc1"                                  # string | image name | extra hostnames, space or comma separated
    entrypoint = ["entrypoint.sh"]                  # array of strings | image default | container entrypoint, one token per element
    command = ["executable", "param1"]              # array of strings | image default | container command, one token per element
    environment = ["ENV1=value1"]                   # array of strings | unset | KEY=value pairs for the service container
```

## Kubernetes executor

`[runners.kubernetes]` applies when `executor` is `kubernetes`. The runner creates one Pod per job with a build
container, a helper container and one container per service, in `namespace`, using `service_account`. Keys
ending in `_overwrite_allowed` or `_overwrite_max_allowed` are the administrator's permission for the matching
`KUBERNETES_*` job variable listed under Kubernetes job overwrite variables. Nested tables each have their own
section below.

### Kubernetes connection, namespace and images

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  host = "https://api.cluster.example.internal:6443"   # string | in-cluster or kubeconfig discovery | API server URL
  context = ""                                   # string | current context | kubeconfig context used when host is unset
  cert_file = "/etc/ssl/kubernetes/api.crt"      # string | unset | client certificate for the API server
  key_file = "/etc/ssl/kubernetes/api.key"       # string | unset | private key of that certificate
  ca_file = "/etc/ssl/kubernetes/ca.crt"         # string | unset | CA bundle that verifies the API server
  bearer_token = ""                              # string | service account token | token used to create job Pods
  bearer_token_overwrite_allowed = false         # bool | false | let a job pass its own token in KUBERNETES_BEARER_TOKEN
  namespace = "gitlab-runner"                    # string | unset | namespace of job Pods; the Operator uses the Runner resource's namespace
  namespace_overwrite_allowed = "ci-.*"          # regex string | unset (off) | pattern a job's KUBERNETES_NAMESPACE_OVERWRITE must match; the namespace must exist and the runner needs core API permissions there (Helm rbac.clusterWideAccess true)
  namespace_per_job = false                      # bool | false | one namespace per job; ignores namespace and namespace_overwrite_allowed
  service_account = "gitlab-runner-jobs"         # string | namespace default | service account of job Pods
  service_account_overwrite_allowed = "ci-.*"    # regex string | unset (off) | pattern a job's KUBERNETES_SERVICE_ACCOUNT_OVERWRITE must match
  automount_service_account_token = false        # bool | cluster default | mount the service account token into the build Pod
  image = "alpine:3.21"                          # string | unset | image for jobs that name none
  image_pull_secrets = ["registry-credentials"]  # array of strings | unset | docker-registry Secrets used to pull images
  use_service_account_image_pull_secrets = false # bool | false | set no imagePullSecrets on the Pod so the service account's apply
  pull_policy = ["always", "if-not-present"]     # string or array | cluster default | never, if-not-present, always; tried in order; applies to build, helper and services
  allowed_pull_policies = ["always", "if-not-present"]   # array | the pull_policy values | pull policies a job may request
  allowed_images = ["registry.example.com/*"]    # array of wildcards | everything | images a job may name
  allowed_services = ["postgres:*"]              # array of wildcards | everything | services a job may name
  helper_image = ""                              # string | registry.gitlab.com/gitlab-org/gitlab-runner/gitlab-runner-helper:<arch>-<revision> | clone and artifact helper image; mirror it for offline clusters
  helper_image_flavor = "alpine"                 # string | alpine | alpine, alpine3.21, alpine-latest or ubuntu
  helper_image_autoset_arch_and_os = false       # bool | false | pick the helper architecture and OS from the node
  privileged = false                             # bool | false | privileged flag on every container
  allow_privilege_escalation = false             # bool | unset (cluster default) | allowPrivilegeEscalation on every container
  host_users = false                             # bool | unset (cluster default) | hostUsers on the Pod; false runs in its own user namespace; forced false with proc_mount unmasked or suspend; 19.6+
  host_users_overwrite_allowed = false           # bool | false | let a job set KUBERNETES_HOST_USERS; 19.6+
  runtime_class_name = "kata"                    # string | unset | RuntimeClass of every Pod; jobs fail when the cluster lacks it
  priority_class_name = "ci-jobs"                # string | cluster default | PriorityClass of job Pods
  scheduler_name = ""                            # string | default scheduler | scheduler that places job Pods
  cap_add = ["SYS_TIME"]                         # array of strings | unset | capabilities added to job Pod containers without the CAP_ prefix; wins over the default drop list
  cap_drop = ["SYS_ADMIN"]                       # array of strings | NET_RAW dropped | capabilities dropped; wins over cap_add for the same name
  allowed_users = ["1000", "65534"]              # array of strings | everything | UIDs a job may request for container users
  allowed_groups = ["1000"]                      # array of strings | everything | GIDs a job may request for container groups
  logs_base_dir = "/tmp"                         # string | unset | prefix of the build log path logs-<project>-<job>
  scripts_base_dir = "/tmp"                      # string | unset | prefix of the build script path scripts-<project>-<job>
```

### Kubernetes pod lifecycle and API retries

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  poll_interval = 3                              # int seconds | 3 | how often the runner checks the Pod it created
  poll_timeout = 180                             # int seconds | 180 | how long a Pod may wait to start; raise it to queue more jobs than the cluster can run at once
  resource_availability_check_max_attempts = 0   # int | 0 (off) | attempts, 5 seconds apart, to see the service account or pull secret before giving up
  cleanup_grace_period_seconds = 30              # int | unset | grace period when the job Pod is deleted after the job
  pod_termination_grace_period_seconds = 30      # int | unset | terminationGracePeriodSeconds of the Pod
  terminationGracePeriodSeconds = 30             # int | unset | deprecated; replaced by the two keys above and ignores them when set
  cleanup_resources_timeout = "5m"               # duration | 5m | total time allowed for resource cleanup after the job
  retry_limit = 5                                # int | 5 | attempts for retryable API errors; backoff starts at 500 ms
  retry_backoff_max = 2000                       # int milliseconds | 2000 | backoff ceiling; not below 500
  print_pod_warning_events = true                # bool | true | print warning events while the Pod starts and after a failure; needs events list
  pod_disruption_budget = false                  # bool | false | one PodDisruptionBudget per job Pod against drains; needs poddisruptionbudgets permissions
  enable_service_links = false                   # bool | true (cluster default) | inject service environment variables in Docker links syntax
  dns_policy = "cluster-first"                   # string | cluster-first | none, default, cluster-first or cluster-first-with-host-net
  suspend_pvc_size = "20Gi"                      # string | 20Gi | PVC size for suspendable job environments (FF_SUSPENDABLE_ENVIRONMENTS)
  suspend_pvc_storage_class = ""                 # string | cluster default | StorageClass of that PVC
  pod_labels_overwrite_allowed = ".*"            # regex string | unset (off) | pattern a job's KUBERNETES_POD_LABELS_* values must match
  pod_annotations_overwrite_allowed = ".*"       # regex string | unset (off) | pattern a job's KUBERNETES_POD_ANNOTATIONS_* values must match
  [runners.kubernetes.retry_limits]              # table of error substring = int | unset | per-error attempts; wins over retry_limit
    "TLS handshake timeout" = 10
    "exceeded quota" = 20
  [runners.kubernetes.pod_labels]                # table of string = string | unset | labels on every job Pod; values expand environment variables; runner.gitlab.com/* is read-only
    team = "platform"
  [runners.kubernetes.pod_annotations]           # table of string = string | unset | annotations on every job Pod; values expand environment variables
    "cluster-autoscaler.kubernetes.io/safe-to-evict" = "false"
```

### Kubernetes resource requests and limits

Each of CPU, memory and ephemeral storage has a request and a limit for the build, helper and service containers
and for the Pod as a whole; each has an `_overwrite_max_allowed` cap that enables the matching job variable.
Values are Kubernetes quantities. Helper containers that cache or upload artifacts want 250Mi or more.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  cpu_request = "500m"                                        # quantity | unset | build container CPU request
  cpu_limit = "1"                                             # quantity | unset | build container CPU limit
  cpu_request_overwrite_max_allowed = "2"                     # quantity | unset (off) | ceiling for KUBERNETES_CPU_REQUEST
  cpu_limit_overwrite_max_allowed = "4"                       # quantity | unset (off) | ceiling for KUBERNETES_CPU_LIMIT
  memory_request = "1Gi"                                      # quantity | unset | build container memory request
  memory_limit = "2Gi"                                        # quantity | unset | build container memory limit
  memory_request_overwrite_max_allowed = "4Gi"                # quantity | unset (off) | ceiling for KUBERNETES_MEMORY_REQUEST
  memory_limit_overwrite_max_allowed = "8Gi"                  # quantity | unset (off) | ceiling for KUBERNETES_MEMORY_LIMIT
  ephemeral_storage_request = "1Gi"                           # quantity | unset | build container ephemeral storage request
  ephemeral_storage_limit = "4Gi"                             # quantity | unset | build container ephemeral storage limit
  ephemeral_storage_request_overwrite_max_allowed = "8Gi"     # quantity | unset (off) | ceiling for KUBERNETES_EPHEMERAL_STORAGE_REQUEST
  ephemeral_storage_limit_overwrite_max_allowed = "16Gi"      # quantity | unset (off) | ceiling for KUBERNETES_EPHEMERAL_STORAGE_LIMIT
  helper_cpu_request = "100m"                                 # quantity | unset | helper container CPU request
  helper_cpu_limit = "500m"                                   # quantity | unset | helper container CPU limit
  helper_cpu_request_overwrite_max_allowed = "1"              # quantity | unset (off) | ceiling for KUBERNETES_HELPER_CPU_REQUEST
  helper_cpu_limit_overwrite_max_allowed = "2"                # quantity | unset (off) | ceiling for KUBERNETES_HELPER_CPU_LIMIT
  helper_memory_request = "250Mi"                             # quantity | unset | helper container memory request
  helper_memory_limit = "250Mi"                               # quantity | unset | helper container memory limit
  helper_memory_request_overwrite_max_allowed = "1Gi"         # quantity | unset (off) | ceiling for KUBERNETES_HELPER_MEMORY_REQUEST
  helper_memory_limit_overwrite_max_allowed = "1Gi"           # quantity | unset (off) | ceiling for KUBERNETES_HELPER_MEMORY_LIMIT
  helper_ephemeral_storage_request = "512Mi"                  # quantity | unset | helper container ephemeral storage request
  helper_ephemeral_storage_limit = "1Gi"                      # quantity | unset | helper container ephemeral storage limit
  helper_ephemeral_storage_request_overwrite_max_allowed = "2Gi"   # quantity | unset (off) | ceiling for KUBERNETES_HELPER_EPHEMERAL_STORAGE_REQUEST
  helper_ephemeral_storage_limit_overwrite_max_allowed = "4Gi"     # quantity | unset (off) | ceiling for KUBERNETES_HELPER_EPHEMERAL_STORAGE_LIMIT
  service_cpu_request = "250m"                                # quantity | unset | service container CPU request
  service_cpu_limit = "1"                                     # quantity | unset | service container CPU limit
  service_cpu_request_overwrite_max_allowed = "2"             # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_CPU_REQUEST
  service_cpu_limit_overwrite_max_allowed = "4"               # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_CPU_LIMIT
  service_memory_request = "512Mi"                            # quantity | unset | service container memory request
  service_memory_limit = "1Gi"                                # quantity | unset | service container memory limit
  service_memory_request_overwrite_max_allowed = "2Gi"        # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_MEMORY_REQUEST
  service_memory_limit_overwrite_max_allowed = "4Gi"          # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_MEMORY_LIMIT
  service_ephemeral_storage_request = "512Mi"                 # quantity | unset | service container ephemeral storage request
  service_ephemeral_storage_limit = "1Gi"                     # quantity | unset | service container ephemeral storage limit
  service_ephemeral_storage_request_overwrite_max_allowed = "2Gi"  # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_EPHEMERAL_STORAGE_REQUEST
  service_ephemeral_storage_limit_overwrite_max_allowed = "4Gi"    # quantity | unset (off) | ceiling for KUBERNETES_SERVICE_EPHEMERAL_STORAGE_LIMIT
  pod_cpu_request = "1"                                       # quantity | unset | Pod-level CPU request; Kubernetes 1.32 alpha, 1.34 beta
  pod_cpu_limit = "4"                                         # quantity | unset | Pod-level CPU limit
  pod_cpu_request_overwrite_max_allowed = "2"                 # quantity | unset (off) | ceiling for KUBERNETES_POD_CPU_REQUEST
  pod_cpu_limit_overwrite_max_allowed = "8"                   # quantity | unset (off) | ceiling for KUBERNETES_POD_CPU_LIMIT
  pod_memory_request = "2Gi"                                  # quantity | unset | Pod-level memory request
  pod_memory_limit = "8Gi"                                    # quantity | unset | Pod-level memory limit
  pod_memory_request_overwrite_max_allowed = "4Gi"            # quantity | unset (off) | ceiling for KUBERNETES_POD_MEMORY_REQUEST
  pod_memory_limit_overwrite_max_allowed = "16Gi"             # quantity | unset (off) | ceiling for KUBERNETES_POD_MEMORY_LIMIT
```

### Kubernetes job overwrite variables

Job variables a `.gitlab-ci.yml` may set when the runner's matching `_overwrite_allowed` regex or
`_overwrite_max_allowed` quantity permits it. Values above the cap, or without a cap, are ignored.

```yaml
variables:
  # Comment: type | runner key that permits it | effect
  KUBERNETES_CPU_REQUEST: "3"                      # quantity | cpu_request_overwrite_max_allowed | build container CPU request
  KUBERNETES_CPU_LIMIT: "5"                        # quantity | cpu_limit_overwrite_max_allowed | build container CPU limit
  KUBERNETES_MEMORY_REQUEST: "2Gi"                 # quantity | memory_request_overwrite_max_allowed | build container memory request
  KUBERNETES_MEMORY_LIMIT: "4Gi"                   # quantity | memory_limit_overwrite_max_allowed | build container memory limit
  KUBERNETES_EPHEMERAL_STORAGE_REQUEST: "512Mi"    # quantity | ephemeral_storage_request_overwrite_max_allowed | build container storage request
  KUBERNETES_EPHEMERAL_STORAGE_LIMIT: "1Gi"        # quantity | ephemeral_storage_limit_overwrite_max_allowed | build container storage limit
  KUBERNETES_HELPER_CPU_REQUEST: "1"               # quantity | helper_cpu_request_overwrite_max_allowed | helper container; the same pattern for HELPER_CPU_LIMIT, HELPER_MEMORY_*, HELPER_EPHEMERAL_STORAGE_*
  KUBERNETES_HELPER_MEMORY_LIMIT: "512Mi"          # quantity | helper_memory_limit_overwrite_max_allowed | helper memory for cache or artifact heavy jobs
  KUBERNETES_SERVICE_CPU_REQUEST: "1"              # quantity | service_cpu_request_overwrite_max_allowed | every service; the same pattern for SERVICE_CPU_LIMIT, SERVICE_MEMORY_*, SERVICE_EPHEMERAL_STORAGE_*; a service's own variables: block overrides per service
  KUBERNETES_POD_CPU_REQUEST: "2"                  # quantity | pod_cpu_request_overwrite_max_allowed | Pod level; the same pattern for POD_CPU_LIMIT and POD_MEMORY_*
  KUBERNETES_NAMESPACE_OVERWRITE: ci-$CI_COMMIT_REF_SLUG   # string | namespace_overwrite_allowed | existing namespace for this job's Pod; the runner needs cluster-wide RBAC to create Pods there
  KUBERNETES_SERVICE_ACCOUNT_OVERWRITE: ci-deployer        # string | service_account_overwrite_allowed | service account of this job's Pod
  KUBERNETES_BEARER_TOKEN: $DEPLOY_TOKEN           # string | bearer_token_overwrite_allowed | token used to create this job's Pod
  KUBERNETES_HOST_USERS: "true"                    # bool | host_users_overwrite_allowed | Pod hostUsers for this job; 19.6+
  KUBERNETES_NODE_SELECTOR_1: "kubernetes.io/arch=arm64"   # key=value | node_selector_overwrite_allowed | adds to or replaces the runner node_selector; any suffix
  KUBERNETES_NODE_TOLERATIONS_1: "node-role.kubernetes.io/master:NoSchedule"   # key=value:Effect | node_tolerations_overwrite_allowed | extra toleration; empty value tolerates all
  KUBERNETES_POD_LABELS_1: "team=platform"         # key=value | pod_labels_overwrite_allowed | label on this job's Pod; runner.gitlab.com/* is read-only
  KUBERNETES_POD_ANNOTATIONS_1: "owner=ci"         # key=value | pod_annotations_overwrite_allowed | annotation on this job's Pod
```

### Kubernetes node selector, tolerations and affinity

`node_selector` decides the operating system and architecture of the node; `affinity` only narrows placement.
Affinity tables mirror the Kubernetes field names in snake case.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  node_selector_overwrite_allowed = ".*"           # regex string | unset (off) | pattern a job's KUBERNETES_NODE_SELECTOR_* values must match
  node_tolerations_overwrite_allowed = ".*"        # regex string | unset (off) | pattern a job's KUBERNETES_NODE_TOLERATIONS_* values must match
  [runners.kubernetes.node_selector]               # table of string = string | unset | Pods go to nodes matching every pair
    "kubernetes.io/os" = "linux"
    "kubernetes.io/arch" = "amd64"
  [runners.kubernetes.node_tolerations]            # table of "key=value" = "Effect" | unset | taints the Pod tolerates; "key" = "" tolerates any value
    "node-role.kubernetes.io/master" = "NoSchedule"
    "dedicated=ci" = "NoSchedule"
    "onlyKey" = ""
  [runners.kubernetes.affinity]
    [runners.kubernetes.affinity.node_affinity]
      [[runners.kubernetes.affinity.node_affinity.preferred_during_scheduling_ignored_during_execution]]
        weight = 100                               # int 1-100 | required in preferred terms | weight of this preference
        [runners.kubernetes.affinity.node_affinity.preferred_during_scheduling_ignored_during_execution.preference]
          [[runners.kubernetes.affinity.node_affinity.preferred_during_scheduling_ignored_during_execution.preference.match_expressions]]
            key = "disktype"                       # string | required | node label key; match_fields takes node fields instead
            operator = "In"                        # string | required | In, NotIn, Exists, DoesNotExist, Gt or Lt
            values = ["ssd"]                       # array of strings | unset | values for In, NotIn, Gt and Lt
      [runners.kubernetes.affinity.node_affinity.required_during_scheduling_ignored_during_execution]
        [[runners.kubernetes.affinity.node_affinity.required_during_scheduling_ignored_during_execution.node_selector_terms]]
          [[runners.kubernetes.affinity.node_affinity.required_during_scheduling_ignored_during_execution.node_selector_terms.match_expressions]]
            key = "topology.kubernetes.io/zone"
            operator = "In"
            values = ["zone-a", "zone-b"]
    [runners.kubernetes.affinity.pod_affinity]     # pod_anti_affinity takes the same shape and keeps Pods apart instead
      [[runners.kubernetes.affinity.pod_affinity.required_during_scheduling_ignored_during_execution]]
        topology_key = "topology.kubernetes.io/zone"   # string | required | node label that defines the co-location domain
        namespaces = ["ci"]                        # array of strings | the Pod's namespace | namespaces whose Pods are matched
        match_label_keys = ["pod-template-hash"]   # array of strings | unset | Kubernetes 1.31+; label keys copied from the incoming Pod into the selector
        mismatch_label_keys = ["tenant"]           # array of strings | unset | Kubernetes 1.31+; label keys that must differ
        [runners.kubernetes.affinity.pod_affinity.required_during_scheduling_ignored_during_execution.label_selector]
          [[runners.kubernetes.affinity.pod_affinity.required_during_scheduling_ignored_during_execution.label_selector.match_expressions]]
            key = "security"
            operator = "In"
            values = ["S1"]
        [runners.kubernetes.affinity.pod_affinity.required_during_scheduling_ignored_during_execution.namespace_selector]
          [[runners.kubernetes.affinity.pod_affinity.required_during_scheduling_ignored_during_execution.namespace_selector.match_expressions]]
            key = "team"
            operator = "In"
            values = ["ci"]
      [[runners.kubernetes.affinity.pod_affinity.preferred_during_scheduling_ignored_during_execution]]
        weight = 100
        [runners.kubernetes.affinity.pod_affinity.preferred_during_scheduling_ignored_during_execution.pod_affinity_term]
          topology_key = "kubernetes.io/hostname"
```

### Kubernetes security contexts

The Pod context sets defaults; each container context overrides them for that container. Values set here bypass
`allowed_users` and `allowed_groups`, which only limit what a job may request. `seccomp_profile` and
`app_armor_profile` take the same shape in every context and replace the deprecated annotations.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  [runners.kubernetes.pod_security_context]
    run_as_user = 59417                        # int | unset | UID of the entrypoint in every container
    run_as_group = 59417                       # int | unset | GID of the entrypoint in every container
    run_as_non_root = true                     # bool | unset | refuse to run as root
    fs_group = 59417                           # int | unset | supplemental group that owns mounted volumes
    supplemental_groups = [1000]               # array of ints | unset | groups added to the first process of each container
    selinux_type = "spc_t"                     # string | unset | SELinux type label of every container
    [runners.kubernetes.pod_security_context.seccomp_profile]
      type = "RuntimeDefault"                  # string | unset | RuntimeDefault, Localhost or Unconfined
      localhost_profile = ""                   # string | unset | profile path on the node; required with Localhost
    [runners.kubernetes.pod_security_context.app_armor_profile]
      type = "RuntimeDefault"                  # string | unset | RuntimeDefault, Localhost or Unconfined; Kubernetes 1.30+
      localhost_profile = ""                   # string | unset | profile name on the node; required with Localhost
  [runners.kubernetes.build_container_security_context]   # helper_container_security_context, service_container_security_context and init_permissions_container_security_context take the same keys
    run_as_user = 65534                        # int | pod context | UID of this container's entrypoint
    run_as_group = 65534                       # int | pod context | GID of this container's entrypoint
    run_as_non_root = true                     # bool | pod context | refuse root in this container
    privileged = false                         # bool | unset | privileged flag on this container only
    allow_privilege_escalation = false         # bool | unset | allowPrivilegeEscalation on this container
    read_only_root_filesystem = false          # bool | unset | read-only root filesystem
    proc_mount = "default"                     # string | default | default or unmasked; unmasked for rootless OCI image builds and forces host_users false
    selinux_type = "spc_t"                     # string | unset | SELinux type label of this container
    [runners.kubernetes.build_container_security_context.capabilities]
      add = ["NET_ADMIN"]                      # array of strings | unset | capabilities added to this container
      drop = ["ALL"]                           # array of strings | unset | capabilities dropped from this container
    [runners.kubernetes.build_container_security_context.seccomp_profile]
      type = "Unconfined"                      # string | pod context | Unconfined with app_armor_profile Unconfined lets BuildKit build rootless images
    [runners.kubernetes.build_container_security_context.app_armor_profile]
      type = "Unconfined"
```

### Kubernetes volumes

Every volume type takes `name`, `mount_path` and optional `sub_path`; the rest is type-specific. Volumes mount
into the build container, and `name` for a PVC may hold `$CI_CONCURRENT_ID` so each concurrent slot gets its
own claim, which is how a persistent build directory and `GIT_STRATEGY=fetch` work on Kubernetes.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  [[runners.kubernetes.volumes.host_path]]
    name = "hostpath-1"                          # string | required | volume name
    mount_path = "/path/in/container"            # string | required | mount point in the container
    sub_path = ""                                # string | volume root | path inside the volume to mount
    host_path = "/path/on/host"                  # string | mount_path | path on the node
    read_only = true                             # bool | false | read-only mount
    mount_propagation = "HostToContainer"        # string | unset | None, HostToContainer or Bidirectional
  [[runners.kubernetes.volumes.pvc]]
    name = "build-pvc-$CI_CONCURRENT_ID"         # string | required | volume name and PersistentVolumeClaim name; variables expand; create one claim per concurrent slot
    mount_path = "/mnt/builds"                   # string | required | mount point; make it builds_dir for a persistent build directory
    sub_path = ""                                # string | volume root | path inside the claim
    read_only = false                            # bool | false | read-only mount
    mount_propagation = ""                       # string | unset | None, HostToContainer or Bidirectional
  [[runners.kubernetes.volumes.config_map]]
    name = "config-map-1"                        # string | required | volume name and ConfigMap name
    mount_path = "/path/to/directory"            # string | required | directory that receives one file per key
    sub_path = ""                                # string | volume root | path inside the volume
    read_only = false                            # bool | false | read-only mount
    [runners.kubernetes.volumes.config_map.items]   # table of key = relative path | every key as a file | only listed keys are mounted; a missing key fails Pod creation
      "key_1" = "relative/path/to/key_1_file"
  [[runners.kubernetes.volumes.secret]]
    name = "secrets"                             # string | required | volume name and Secret name
    mount_path = "/path/to/directory1"           # string | required | directory that receives one file per key
    sub_path = ""                                # string | volume root | path inside the volume
    read_only = true                             # bool | false | read-only mount
    [runners.kubernetes.volumes.secret.items]    # table of key = relative path | every key as a file | only listed keys are mounted
      "secret_1" = "relative/path/to/secret_1_file"
  [[runners.kubernetes.volumes.empty_dir]]
    name = "empty-dir"                           # string | required | volume name
    mount_path = "/path/to/empty_dir"            # string | required | mount point
    sub_path = ""                                # string | volume root | path inside the volume
    medium = "Memory"                            # string | "" (node disk) | Memory gives a tmpfs
    size_limit = "1Gi"                           # quantity | unset | local storage the volume may use
    mount_propagation = ""                       # string | unset | None, HostToContainer or Bidirectional
  [[runners.kubernetes.volumes.csi]]
    name = "csi-volume"                          # string | required | volume name
    mount_path = "/path/to/csi/volume"           # string | required | mount point
    driver = "my-csi-driver"                     # string | required | CSI driver name
    fs_type = "ext4"                             # string | driver default | filesystem type
    read_only = false                            # bool | false | read-only mount
    sub_path = ""                                # string | volume root | path inside the volume
    [runners.kubernetes.volumes.csi.volume_attributes]   # table of string = string | unset | driver attributes
      size = "2Gi"
  [[runners.kubernetes.volumes.nfs]]
    name = "nfs"                                 # string | required | volume name
    mount_path = "/path/to/mount/point"          # string | required | mount point
    server = "nfs.example.internal"              # string | required | NFS server
    path = "/path/on/nfs-share"                  # string | required | exported path
    read_only = false                            # bool | false | read-only mount
    sub_path = ""                                # string | volume root | path inside the share
```

### Kubernetes pod spec patches

`pod_spec` rewrites the generated Pod specification after every other key has been applied. It is in beta and
needs the `FF_USE_ADVANCED_POD_SPEC_CONFIGURATION` feature flag. One entry holds either `patch` or
`patch_path`, not both; entries apply in order. The Operator exposes the same idea as `spec.podSpec` on the
runner Pod, with the keys `name`, `patch`, `patchFile` and `patchType`.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  [[runners.kubernetes.pod_spec]]
    name = "hostname"                          # string | required | label of this patch in logs
    patch = '''
      hostname: "custom-pod-hostname"
    '''                                        # string | unset | JSON or YAML patch text applied to the PodSpec
    patch_type = "merge"                       # string | strategic | merge (RFC 7386), json (RFC 6902) or strategic (Kubernetes strategic merge)
  [[runners.kubernetes.pod_spec]]
    name = "build-pvc"
    patch_path = "/etc/gitlab-runner/pod-spec/build-pvc.yaml"   # string | unset | file holding the patch instead of patch
    patch_type = "strategic"
  [[runners.kubernetes.pod_spec]]
    name = "terminationGracePeriodSeconds"
    patch = '''
      [{"op": "replace", "path": "/terminationGracePeriodSeconds", "value": 60}]
    '''
    patch_type = "json"
```

### Kubernetes services, lifecycle hooks, DNS and host aliases

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  [[runners.kubernetes.services]]                    # one sidecar container per entry in every job Pod
    name = "postgres:16-alpine"                      # string | required | service image
    alias = "db"                                     # string | image name | container and DNS names, space or comma separated
    entrypoint = ["docker-entrypoint.sh"]            # array of strings | image default | container entrypoint
    command = ["postgres"]                           # array of strings | image default | container command
    environment = ["POSTGRES_PASSWORD=from-a-secret", "HEALTHCHECK_TCP_PORT=5432"]   # array of strings | unset | KEY=value pairs; HEALTHCHECK_TCP_PORT delays the script until the port answers
    suspend_data_path = "/var/lib/postgresql/data"   # string | unset | data path kept on the suspend PVC across suspend and resume; only without command or entrypoint
  [runners.kubernetes.container_lifecycle]           # post_start and pre_stop each take one of exec, http_get or tcp_socket
    [runners.kubernetes.container_lifecycle.post_start.exec]
      command = ["touch", "/builds/postStart.txt"]   # array of strings | required | command run inside the container
    [runners.kubernetes.container_lifecycle.pre_stop.http_get]
      port = 8080                                    # int | required | container port
      host = "localhost"                             # string | Pod IP | host to connect to
      path = "/drain"                                # string | unset | request path
      scheme = "HTTP"                                # string | HTTP | HTTP or HTTPS
      [[runners.kubernetes.container_lifecycle.pre_stop.http_get.http_headers]]
        name = "X-Drain"                             # string | required | header name
        value = "1"                                  # string | required | header value
  [runners.kubernetes.dns_config]
    nameservers = ["192.0.2.53"]                     # array of strings | unset | DNS servers of the Pod
    searches = ["ns1.svc.cluster-domain.example"]    # array of strings | unset | search domains of the Pod
    [[runners.kubernetes.dns_config.options]]
      name = "ndots"                                 # string | required | resolver option name
      value = "2"                                    # string | unset | resolver option value
  [[runners.kubernetes.host_aliases]]
    ip = "192.0.2.10"                                # string | required | address written to /etc/hosts in every container
    hostnames = ["registry.example.internal"]        # array of strings | required | names mapped to that address
```

A `tcp_socket` handler takes `port` and optional `host` instead of the `http_get` keys.

### Kubernetes pause pods

`[runners.kubernetes.autoscaler]` keeps low-priority pause Pods running so job Pods preempt them and start
without waiting for the cluster autoscaler. Pause Pods do not inherit `pod_labels` or `pod_annotations`.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.kubernetes]
  [runners.kubernetes.autoscaler]                        # needs a ClusterRole: apps/deployments get, list, create, update, delete; scheduling.k8s.io/priorityclasses get, create
    max_pause_pods = 10                                  # int | 0 (unlimited) | ceiling on pause Pods
    pause_pod_image = "registry.k8s.io/pause:3.10"       # string | registry.k8s.io/pause:3.10 | pause Pod image; mirror it for offline clusters
    pause_pod_priority_class_name = "gitlab-runner-idle-capacity"   # string | gitlab-runner-idle-capacity, auto-created with priority -1 | lower than job Pods so they are preempted; a set value skips auto-creation
    pause_pod_image_pull_secrets = ["registry-credentials"]   # array of strings | unset | pull secrets for the pause image
    [runners.kubernetes.autoscaler.pause_pod_labels]     # table of string = string | unset | labels on pause Pods; runner.gitlab.com/* ignored; 19.6+
      team = "platform"
    [runners.kubernetes.autoscaler.pause_pod_annotations]   # table of string = string | unset | annotations on pause Pods; 19.6+
      "cluster-autoscaler.kubernetes.io/safe-to-evict" = "true"
    [[runners.kubernetes.autoscaler.policy]]             # the last policy whose periods match now wins
      periods = ["* 8-17 * * mon-fri"]                   # array of cron strings | "* * * * *" | when this policy applies
      timezone = "UTC"                                   # string | system local | time zone of the cron fields
      idle_count = 5                                     # int | 0 | pause Pods to keep
      idle_time = "5m"                                   # duration | 5m | wait before removing pause Pods after desired capacity drops
      scale_factor = 0.5                                 # float | 0 (off) | pause Pods = max(idle_count, active_jobs x scale_factor)
      scale_factor_limit = 20                            # int | 0 (no limit) | ceiling for the scale_factor result
    [[runners.kubernetes.autoscaler.policy]]
      periods = ["* * * * *"]
      idle_count = 0
```

## Caching

`[runners.cache]` sends job caches to object storage so every runner and executor instance shares them. One
backend table matches `Type`. Compression and transfer tuning are job variables; see Speed up and optimization.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.cache]
  Type = "s3"                                # string | unset (local cache only) | s3, gcs or azure
  Path = "path/to/prefix"                    # string | unset | prefix inside the bucket or container
  Shared = false                             # bool | false | share cache objects between runners instead of one prefix per runner
  MaxUploadedArchiveSize = 5368709120        # int bytes | unset | reject larger archives; GCS enforces it in the signed URL; also set the limit at the provider
  RedactURL = false                          # bool | false | hide cache upload and download URLs from the job log
```

### S3 cache

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.cache]
  Type = "s3"
  [runners.cache.s3]
    ServerAddress = "s3.amazonaws.com"       # string | unset | host:port of the S3-compatible endpoint; a MinIO or Silo host:port here with Insecure for plain HTTP
    AccessKey = ""                           # string | unset | access key; omit with IAM authentication
    SecretKey = ""                           # string | unset | secret key; omit with IAM authentication
    SessionToken = ""                        # string | unset | session token for temporary credentials
    BucketName = "runners-cache"             # string | unset | bucket
    BucketLocation = "eu-west-1"             # string | unset | region; keep it the region of the runners
    Insecure = false                         # bool | false | plain HTTP to ServerAddress
    AuthenticationType = "iam"               # string | access-key when ServerAddress, AccessKey and SecretKey are all set, else iam | iam uses the AWS SDK credential chain
    ServerSideEncryption = "KMS"             # string | unset | S3, KMS or DSSE-KMS
    ServerSideEncryptionKeyID = "alias/my-key"   # string | unset | KMS key alias (alias/ prefix), ID or ARN; ARN for cross-account keys
    DualStack = true                         # bool | true | IPv4 and IPv6 endpoints; ignored with ServerAddress; off for S3 Express
    Accelerate = false                       # bool | false, true when ServerAddress is an accelerated endpoint | S3 Transfer Acceleration
    PathStyle = false                        # bool | detected from ServerAddress | path-style addressing
    RoleARN = "arn:aws:iam::123456789012:role/runner-cache"   # string | unset | AssumeRole for time-limited GetObject and PutObject; enables multipart transfers past 5 GB
    UploadRoleARN = ""                       # string | unset | deprecated; RoleARN replaces it
    AssumeRoleMaxConcurrency = 5             # int | 5 | parallel AssumeRole calls to STS; -1 removes the limit
    DisableAssumeRoleCredentialsCaching = false   # bool | false | fetch fresh AssumeRole credentials for every request
```

### GCS cache

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.cache]
  Type = "gcs"
  [runners.cache.gcs]
    CredentialsFile = "/etc/gitlab-runner/service-account.json"   # string | unset | service_account JSON key file; wins over AccessID and PrivateKey
    AccessID = "cache@project.iam.gserviceaccount.com"   # string | unset | service account ID
    PrivateKey = ""                          # string | unset | key that signs requests; omit both with Application Default Credentials and the signBlob permission
    BucketName = "runners-cache"             # string | unset | bucket
    UniverseDomain = "googleapis.com"        # string | googleapis.com | universe domain for Google Cloud Dedicated or custom universes
```

Workload identity and Application Default Credentials need no key here; the default adapter uses the GCS SDK,
and `FF_USE_LEGACY_GCS_CACHE_ADAPTER` restores the old signing path.

### Azure cache

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.cache]
  Type = "azure"
  [runners.cache.azure]
    AccountName = "runnercache"              # string | unset | storage account
    AccountKey = ""                          # string | unset | account access key; omit for workload or managed identity
    ContainerName = "runners-cache"          # string | unset | blob container
    StorageDomain = "blob.core.windows.net"  # string | blob.core.windows.net | endpoint domain for sovereign clouds
```

## Autoscaler

`[runners.autoscaler]` drives the `instance` and `docker-autoscaler` executors through a fleeting plugin. Set
`concurrent` to `max_instances x capacity_per_instance`. Policy entries reload on configuration change; the rest
needs a restart. The six bucket arrays come from the runner's configuration types and shape the autoscaler
histograms; unset keeps the runner's built-in buckets.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.autoscaler]
  plugin = "aws:latest"                        # string | required | fleeting plugin name and version, or a path to the plugin binary
  capacity_per_instance = 1                    # int | unset | jobs one instance runs at once
  max_use_count = 1                            # int | 0 (unlimited) | jobs after which an instance is removed; 1 gives a fresh instance per job
  max_instances = 10                           # int | 0 (unlimited) | instances in any state, pending and deleting included
  delete_instances_on_shutdown = false         # bool | false | delete every provisioned instance when the runner stops
  instance_ready_command = "cloud-init status --wait"   # string | unset | run on each new instance; a failure removes it; backoff protects against churn
  instance_acquire_timeout = "15m"             # duration | 15m | wait for an instance before a job acquisition times out
  update_interval = "1m"                       # duration | 1m | polling of the plugin for instance state
  update_interval_when_expecting = "2s"        # duration | 2s | polling while a state change is expected, such as pending to running
  deletion_retry_interval = "1m"               # duration | 1m | wait before retrying a deletion that had no effect
  shutdown_deletion_interval = "10s"           # duration | 10s | wait between deleting instances and checking them during shutdown
  shutdown_deletion_retries = 3                # int | 3 | deletion checks during shutdown
  failure_threshold = 3                        # int | 3 | consecutive health failures before an instance is replaced; pairs with FF_USE_FLEETING_ACQUIRE_HEARTBEATS
  reservation_throttling = true                # bool | runner default | from the configuration types; the page documents no effect
  log_internal_ip = false                      # bool | false | print the instance internal IP in the job log
  log_external_ip = false                      # bool | false | print the instance external IP in the job log
  instance_operation_time_buckets = [1, 5, 30, 120]   # array of floats | runner default | fallback buckets for the four operation histograms below
  instance_creation_time_buckets = []          # array of floats | instance_operation_time_buckets | creation histogram buckets
  instance_is_running_time_buckets = []        # array of floats | instance_operation_time_buckets | time-to-running histogram buckets
  instance_deletion_time_buckets = []          # array of floats | instance_operation_time_buckets | deletion histogram buckets
  instance_readiness_time_buckets = []         # array of floats | instance_operation_time_buckets | readiness histogram buckets
  instance_life_duration_buckets = []          # array of floats | runner default | instance lifetime histogram buckets
  [runners.autoscaler.plugin_config]           # table | unset | re-encoded to JSON and handed to the plugin; keys come from that plugin's documentation
    name = "ci-asg"
    region = "eu-west-1"
  [runners.autoscaler.scale_throttle]          # token quota: burst is the quota size, limit refills it per second
    limit = 10                                 # int per second | 0 (means 100) | new instances per second once the quota is spent; -1 unlimited
    burst = 60                                 # int | max_instances, else limit | instances that may be created at once
  [runners.autoscaler.state_storage]           # beta; without it existing instances are deleted at startup
    enabled = false                            # bool | false | persist instance state on local disk so instances survive a runner restart
    dir = ""                                   # string | .taskscaler beside config.toml | state directory, one subdirectory per runner entry
    keep_instance_with_acquisitions = false    # bool | false | keep restored instances that still hold jobs instead of deleting them
```

### Autoscaler policy and idle instances

`idle_count` counts job slots, not machines. The taskscaler removes an instance whose idle time, counted from its
last job or its provisioning, exceeds `idle_time`, unless it is needed for `idle_count`. Several policies may
exist; the last one whose `periods` match now wins.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[[runners.autoscaler.policy]]
  periods = ["* * * * *"]                      # array of cron strings (minute hour day month weekday) | "* * * * *" | when this policy applies
  timezone = "UTC"                             # string | system local | time zone of the cron fields
  idle_count = 0                               # int | unset | idle job capacity kept ready
  idle_time = "0s"                             # duration | unset | idle duration after which an instance is removed
  scale_factor = 0.0                           # float | 0.0 | extra idle capacity as a factor of in-use capacity; idle_count becomes the minimum
  scale_factor_limit = 0                       # int | unset | ceiling of the scale_factor result
  preemptive_mode = false                      # bool | runner default | true requests a job only once an instance is confirmed available

[[runners.autoscaler.policy]]
  periods = ["* 8-15 * * mon-fri"]
  idle_count = 1
  idle_time = "30m"
```

### Autoscaler connector and VM isolation

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.autoscaler.connector_config]          # overrides or completes what the plugin reports for connecting to instances
  os = "linux"                                 # string | plugin | instance operating system
  arch = "amd64"                               # string | plugin | instance architecture
  protocol = "ssh"                             # string | ssh, winrm on Windows | ssh, winrm or winrm+https
  protocol_port = 22                           # int | 22 ssh, 5985 winrm, 5986 winrm+https | connection port
  username = "ec2-user"                        # string | plugin | login user
  password = ""                                # string | unset | login password
  key_path = ""                                # string | unset | key used to connect or to provision credentials dynamically
  use_static_credentials = false               # bool | false | true disables automatic credential provisioning
  keepalive = "30s"                            # duration | unset | connection keepalive
  timeout = "10m"                              # duration | unset | connection timeout
  use_external_addr = false                    # bool | false | connect to the external address the plugin reports when it has one
[runners.autoscaler.vm_isolation]              # instance executor on macOS through the nesting daemon
  enabled = false                              # bool | false | run each job in a nested VM
  nesting_host = ""                            # string | unset | nesting daemon address
  image = ""                                   # string | unset | VM image when the job names none; [runners.instance] allowed_images limits job choices
  [runners.autoscaler.vm_isolation.nesting_config]   # table | unset | serialized to JSON for the nesting daemon
    cpu = 4
  [runners.autoscaler.vm_isolation.connector_config]   # same keys as connector_config, used for the nested VM
    username = "admin"
```

### Docker Machine autoscaling

`[runners.machine]` is the `docker+machine` executor's autoscaling; the newer executors use `[runners.autoscaler]`.
Idle machines are removed after `IdleTime`; the global `[machine.shutdown_drain]` removes them at shutdown.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.machine]
  MachineDriver = "google"                     # string | required | Docker Machine driver
  MachineName = "auto-scale-%s"                # string | required | machine name template; %s becomes the unique identifier
  MachineOptions = ["google-project=my-project", "google-zone=us-central1-a"]   # array of strings | unset | driver options; FF_MACHINE_OPTIONS_EXPAND_ENV expands $VAR
  IdleCount = 5                                # int | unset | machines kept idle and ready
  IdleTime = 600                               # int seconds | unset | idle duration before a machine is removed
  IdleScaleFactor = 1.5                        # float | 0.0 | idle machines as a factor of machines in use, bounded by IdleCountMin and IdleCount
  IdleCountMin = 1                             # int | 1 | minimum idle machines while IdleScaleFactor is in use
  MaxBuilds = 100                              # int | unset | jobs after which a machine is removed
  MaxGrowthRate = 0                            # int | 0 (no limit) | machines added in parallel
  HeartbeatInterval = "30m"                    # duration | 0 (off) | refresh a runner_manager_heartbeat label on each machine so external cleanup can spot orphans; google driver
  WarmupIdleFraction = 0.0                     # float 0-1 | 0.0 (off) | experiment; fraction of IdleCount built during warm-up regardless of use
  WarmupDuration = "10m"                       # duration | 10m | experiment; warm-up length from the first acquisition
  WarmupReadyFraction = 0.8                    # float 0-1 | 0.8 | experiment; fraction of the warm-up target booted before /health/ready passes
  RemoveWithoutStop = false                    # bool | false | experiment; docker-machine rm without stop first, saving one API call on providers whose delete stops
  [[runners.machine.autoscaling]]              # overrides per period; the last matching entry wins
    Periods = ["* * 9-17 * * mon-fri *"]       # array of cron strings (second minute hour day month weekday year) | unset | when this entry applies
    Timezone = "UTC"                           # string | system local | time zone of the cron fields
    IdleCount = 50                             # int | unset | idle machines in this period
    IdleCountMin = 5                           # int | 1 | minimum idle machines with IdleScaleFactor
    IdleScaleFactor = 1.5                      # float | 0.0 | experiment; idle machines as a factor of machines in use
    IdleTime = 3600                            # int seconds | unset | idle duration before removal in this period
```

## Custom, shell, SSH, instance and VM executors

`[runners.custom]` hands each stage to your own executables. Timeouts are seconds.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.custom]
  config_exec = "/opt/runner/config.sh"      # string | unset | prints JSON that overrides [[runners]] settings before the job starts
  config_args = ["--env", "prod"]            # array of strings | unset | first arguments of config_exec
  config_exec_timeout = 3600                 # int seconds | 3600 | deadline of config_exec
  prepare_exec = "/opt/runner/prepare.sh"    # string | unset | prepares the environment
  prepare_args = []                          # array of strings | unset | first arguments of prepare_exec
  prepare_exec_timeout = 3600                # int seconds | 3600 | deadline of prepare_exec
  run_exec = "/opt/runner/run.sh"            # string | required | runs each generated script (clone, build, cache, artifacts) in the environment
  run_args = []                              # array of strings | unset | first arguments of run_exec
  cleanup_exec = "/opt/runner/cleanup.sh"    # string | unset | tears the environment down
  cleanup_args = []                          # array of strings | unset | first arguments of cleanup_exec
  cleanup_exec_timeout = 3600                # int seconds | 3600 | deadline of cleanup_exec
  graceful_kill_timeout = 600                # int seconds | 600 | wait for prepare_exec and cleanup_exec after SIGTERM, such as on cancel, before SIGKILL
  force_kill_timeout = 600                   # int seconds | 600 | wait after SIGKILL before the runner abandons the process
```

The `shell` executor runs on the host with the `shell` named in `[[runners]]`: `bash` is the Unix default with
`sh` as fallback, `pwsh` the Windows default for new registrations and the `shell` executor, `powershell` the
Windows default for `kubernetes` and `docker-windows`; `cmd` is legacy. `FF_POSIXLY_CORRECT_ESCAPES` switches
bash and sh to POSIX double-quote escaping for shells such as dash.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[runners.ssh]
  host = "build-host.example.internal"       # string | required | host to connect to
  port = "22"                                # string | 22 | port
  user = "ci"                                # string | required | login user
  password = ""                              # string | unset | login password
  identity_file = "/etc/gitlab-runner/id_ed25519"   # string | unset | unencrypted private key
  disable_strict_host_key_checking = false   # bool | false | skip host key checks
  known_hosts_file = ""                      # string | ~/.ssh/known_hosts | known_hosts file for the check
[runners.instance]                           # instance executor with VM isolation
  allowed_images = ["ubuntu-24.04"]          # array of strings | unset | images a job may select when VM isolation is on
  use_common_build_dir = false               # bool | false | one build directory for every job; only with VM isolation or max_use_count 1
[runners.parallels]
  base_name = "my-parallels-image"           # string | required | VM that is cloned
  template_name = ""                         # string | unset | linked template name
  disable_snapshots = false                  # bool | false | true destroys the VM after the job instead of restoring a snapshot
  allowed_images = ["^allowed_vm[1-2]$"]     # array of regexes | base_name only | images a job may select
[runners.virtualbox]
  base_name = "my-virtualbox-image"          # string | required | VM that is cloned; vboxmanage must be on PATH
  base_snapshot = ""                         # string | current snapshot | snapshot name or UUID for the linked clone
  base_folder = ""                           # string | default VM folder | where the clone is created
  disable_snapshots = false                  # bool | false | true makes a full clone and destroys it after the job
  allowed_images = [".*"]                    # array of regexes | base_name only | images a job may select
  start_type = "headless"                    # string | headless | headless, gui or separate
```

## Feature flags

Three ways to set one: a job variable `FF_NAME: 1`, the runner `environment = ["FF_NAME=1"]` for every job, or
the `[runners.feature_flags]` table, which jobs cannot override. Comment: default | effect; deprecated flags
name the release that removes them.

```toml native-config=gitlab-runner/config.toml
[runners.feature_flags]
FF_NETWORK_PER_BUILD = false                     # docker: one network per build; CI_BUILD_NETWORK_NAME names it
FF_USE_LEGACY_KUBERNETES_EXECUTION_STRATEGY = false  # false = attach instead of exec for remote commands; needs SA permissions
FF_USE_DIRECT_DOWNLOAD = true                    # first artifact download redirects to object storage when allowed; retries use configured mode
FF_USE_FASTZIP = true                            # deprecated, removed in 19.7; false = legacy zip for cache and artifacts
FF_DISABLE_UMASK_FOR_DOCKER_EXECUTOR = false     # drop umask 0000; detect image UID/GID and chown instead (image needs `id`)
FF_ENABLE_BASH_EXIT_CODE_CHECK = false           # bash checks each command's exit code instead of relying on set -e
FF_USE_WINDOWS_LEGACY_PROCESS_STRATEGY = false   # deprecated, removed in 19.8; legacy process setup for Windows shell/custom executors
FF_USE_LEGACY_BASH_EVAL = false                  # true = legacy pipeline form for bash eval; default avoids script leaks on cancel
FF_USE_POWERSHELL_PATH_RESOLVER = false          # PowerShell resolves paths itself instead of OS-specific filepath functions
FF_USE_DYNAMIC_TRACE_FORCE_SEND_INTERVAL = false # log force-send interval follows the trace update interval
FF_SCRIPT_SECTIONS = false                       # multi-line script commands become collapsible log sections
FF_ENABLE_JOB_CLEANUP = false                    # clean project dir at build end: delete for GIT_CLONE, git clean for GIT_FETCH
FF_KUBERNETES_HONOR_ENTRYPOINT = false           # honor the image entrypoint unless legacy execution strategy is true
FF_POSIXLY_CORRECT_ESCAPES = false               # POSIX shell escapes instead of bash ANSI-C quoting
FF_RESOLVE_FULL_TLS_CHAIN = false                # deprecated, removed in 20.0; resolve full chain for CI_SERVER_TLS_CA_FILE
FF_DISABLE_POWERSHELL_STDIN = false              # pass PowerShell scripts by file, not stdin; needed for allow_failure:exit_codes
FF_USE_POD_ACTIVE_DEADLINE_SECONDS = true        # pod activeDeadlineSeconds = job timeout
FF_USE_ADVANCED_POD_SPEC_CONFIGURATION = false   # beta: pod_spec patches rewrite the generated Pod specification
FF_SECRET_RESOLVING_FAILS_IF_MISSING = true      # a missing secret value fails resolution
FF_PRINT_POD_EVENTS = false                      # print build pod events until the pod starts
FF_USE_GIT_BUNDLE_URIS = true                    # git transfer.bundleURI = true; false disables bundle support
FF_USE_GIT_NATIVE_CLONE = false                  # GIT_STRATEGY=clone uses git-clone (Git 2.49+) instead of init+fetch
FF_USE_DUMB_INIT_WITH_KUBERNETES_EXECUTOR = false  # run scripts under dumb-init as PID 1 in helper and build containers
FF_USE_INIT_WITH_DOCKER_EXECUTOR = false         # start containers with --init (tini as PID 1)
FF_LOG_IMAGES_CONFIGURED_FOR_JOB = false         # log job image and service image names per job
FF_USE_DOCKER_AUTOSCALER_DIAL_STDIO = true       # tunnel to remote Docker via `docker system dial-stdio`; false = SSH tunnel / fleeting proxy
FF_CLEAN_UP_FAILED_CACHE_EXTRACT = false         # detect failed cache extraction and remove partial contents
FF_USE_WINDOWS_JOB_OBJECT = false                # one Windows job object per process; closing it force-kills stragglers
FF_TIMESTAMPS = true                             # false = no timestamp prefix on log lines
FF_DISABLE_AUTOMATIC_TOKEN_ROTATION = false      # no automatic token rotation; warn near expiry
FF_USE_LEGACY_GCS_CACHE_ADAPTER = false          # legacy GCS cache adapter; default uses the GCS SDK (workload identity)
FF_DISABLE_UMASK_FOR_KUBERNETES_EXECUTOR = false # drop umask 0000 for kubernetes jobs; chown working dir to detected UID/GID
FF_USE_LEGACY_S3_CACHE_ADAPTER = false           # legacy S3 cache adapter; default uses the AWS SDK (custom STS endpoints)
FF_GIT_URLS_WITHOUT_TOKENS = false               # no job token in git config/commands; credential helper reads it from env
FF_WAIT_FOR_POD_TO_BE_REACHABLE = false          # wait for pod Running and ready with certificates attached
FF_MASK_ALL_DEFAULT_TOKENS = true                # mask all default token patterns
FF_EXPORT_HIGH_CARDINALITY_METRICS = false       # export high-cardinality metrics (large data volume)
FF_USE_FLEETING_ACQUIRE_HEARTBEATS = false       # check fleeting instance connectivity before assigning a job
FF_USE_EXPONENTIAL_BACKOFF_STAGE_RETRY = true    # stage retries back off exponentially from 5s to 5m
FF_USE_ADAPTIVE_REQUEST_CONCURRENCY = true       # request_concurrency is a maximum; actual follows successful request rate
FF_USE_GITALY_CORRELATION_ID = true              # add X-Gitaly-Correlation-ID to Git HTTP requests
FF_USE_GIT_PROACTIVE_AUTH = false                # http.proactiveAuth=basic on clone/fetch: send credentials before a 401
FF_HASH_CACHE_KEYS = false                       # SHA256-hash cache keys for local and distributed caches
FF_ENABLE_JOB_INPUTS_INTERPOLATION = true        # interpolate job inputs
FF_USE_JOB_ROUTER = false                        # fetch jobs through Job Router instead of GitLab directly
FF_SCRIPT_TO_STEP_MIGRATION = false              # run user scripts as steps through the step-runner
FF_USE_PARALLEL_CACHE_TRANSFER = false           # parallel object-storage cache transfers; tune CACHE_CONCURRENCY, CACHE_CHUNK_SIZE
FF_USE_PARALLEL_ARTIFACT_TRANSFER = false        # parallel Range GETs for direct-download artifacts (backend must return 206)
FF_CONCRETE = false                              # migrate traditional script execution to the step-runner
FF_SUSPENDABLE_ENVIRONMENTS = false              # allow suspending and resuming job environments
FF_USE_NATIVE_CONTAINER_STOP = false             # engine-native stop on cancel; SIGTERM reaches PID 1 only
FF_MACHINE_OPTIONS_EXPAND_ENV = false            # expand $VAR in [runners.machine] MachineOptions from the runner environment
FF_DOCKER_MACHINE_JSON_OUTPUT = false            # docker-machine create with JSON logging (0.16.2-gitlab.55+); per-phase timing
```

## Concurrency

A job travels: GitLab queue, then a `[[runners]]` worker request, then a slot under the process cap `concurrent`
and the entry cap `limit`, then executor capacity. Each stage has its own key, and a bottleneck sits at the first
stage that is full.

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect on throughput
concurrent = 20                      # int | 1 | process cap on running jobs across every entry; it holds even when the limits add up to more; 0 exits
check_interval = 3                   # int seconds | 3 | request period shared by the entries: each worker sleeps check_interval / number of entries between requests
[[runners]]
  limit = 10                         # int | 0 (unlimited) | cap for this entry; effective cap = min(limit, concurrent minus jobs on other entries)
  request_concurrency = 4            # int | 1 | parallel job requests per worker; raise when jobs wait while concurrent is not reached
  strict_check_interval = false      # bool | false | default re-polls at once after a job until limit, concurrent or an empty queue; true polls once per interval
  [runners.feature_flags]
    FF_USE_ADAPTIVE_REQUEST_CONCURRENCY = true   # bool | true | request_concurrency becomes a ceiling; the live count follows the successful request rate
  [runners.autoscaler]
    capacity_per_instance = 1        # int | unset | jobs per instance; concurrent = max_instances x capacity_per_instance (10 x 1 = 10, 10 x 5 = 50, 4 x 2 = 8)
    max_instances = 20               # int | 0 (unlimited) | instance ceiling counting pending and deleting instances; 20 x 1 matches concurrent above
    max_use_count = 1                # int | 0 (unlimited) | retire an instance after this many jobs
    [[runners.autoscaler.policy]]
      idle_count = 2                 # int | unset | idle job slots kept warm; counts jobs, not machines
      idle_time = "20m"              # duration | unset | idle slot lifetime before its instance is removed
      scale_factor = 0.5             # float | 0.0 | extra idle slots = in-use capacity x factor, up to scale_factor_limit
    [runners.autoscaler.scale_throttle]
      limit = 1                      # int per second | 0 (means 100) | refill rate of the instance-creation quota
      burst = 60                     # int | max_instances or limit | quota size: 60 instances at once, then 1 per second
  [runners.kubernetes]
    poll_timeout = 3600              # int seconds | 180 | how long a job Pod may stay pending; the cluster scheduler, not the runner, decides when it runs
```

Arithmetic the pages give:

- Two entries and `check_interval = 10` make one request every 5 seconds alternating between the entries.
- `concurrent = 10` and two entries with `limit = 10` each: the second entry gets what the first leaves free.
- `unhealthy_requests_limit` 403 responses in a row stop a worker for 30 seconds, then growing waits up to
  `unhealthy_interval` (1h); `gitlab_runner_worker_healthy` reads 0 meanwhile.
- Kubernetes: no executor cap exists, so `concurrent` is the cap; the per-concurrency PVC uses
  `$CI_CONCURRENT_ID`, so create one claim per slot `0` to `concurrent - 1`; `[runners.custom_build_dir]`
  with `concurrent > 1` needs `$CI_CONCURRENT_ID` in `GIT_CLONE_PATH`; pause pods pre-warm with `idle_count`.
- Docker Machine: `IdleCount`, `IdleTime` and `MaxBuilds` play the policy's role; `MaxGrowthRate` caps parallel
  creation; `[machine.shutdown_drain]` removes idle machines at shutdown.
- Operator: `spec.concurrent` defaults to 10 and 0 is not unlimited; `spec.interval` is `check_interval`
  (30); `request_concurrency` is only reachable through the `RUNNER_REQUEST_CONCURRENCY` environment variable.

## Speed up and optimization

Job variables first; set them in `variables:` of one job, of the pipeline, or in the runner's `environment` for
every job. Then the runner-side keys they pair with.

```yaml
variables:
  # Comment: type | default | effect
  GIT_STRATEGY: fetch                           # clone, fetch, none, empty | project setting | fetch reuses the worktree (shell, docker, persistent PVC); clone starts clean; none skips Git; empty recreates the directory
  GIT_DEPTH: "20"                               # int | project setting, 20 for new projects | shallow depth for fetch and clone; too small fails retries of older commits
  GIT_CHECKOUT: "true"                          # bool | true | false updates the repository without checking out the pipeline revision
  GIT_CLEAN_FLAGS: -ffdx                        # string | -ffdx | git clean flags after checkout; none skips it; off with GIT_CHECKOUT false
  GIT_FETCH_EXTRA_FLAGS: --prune --quiet        # string | --prune --quiet | appended after depth, refspecs and origin; none keeps only those
  GIT_CLONE_EXTRA_FLAGS: --reference-if-available /tmp/mirror   # string | unset | appended to native git clone; needs FF_USE_GIT_NATIVE_CLONE and GIT_STRATEGY clone
  GIT_SUBMODULE_STRATEGY: none                  # none, normal, recursive | none | which submodules are initialized
  GIT_SUBMODULE_UPDATE_FLAGS: --jobs 4          # string | unset | appended after --init, --recursive and --depth; --jobs parallelizes
  GIT_SUBMODULE_PATHS: ":(exclude)vendor/big"   # string | all | paths to sync; :(exclude)path skips one
  GIT_SUBMODULE_DEPTH: "20"                     # int | GIT_DEPTH | submodule depth; 0 full history
  GIT_SUBMODULE_FORCE_HTTPS: "false"            # bool | false | rewrite git and ssh submodule URLs to https with the job token
  GIT_LFS_SKIP_SMUDGE: "false"                  # bool | false | keep LFS pointers instead of running git lfs pull
  GIT_CLONE_PATH: $CI_BUILDS_DIR/$CI_CONCURRENT_ID/project   # path under $CI_BUILDS_DIR | unique subpath | needs [runners.custom_build_dir] enabled; expands once
  GET_SOURCES_ATTEMPTS: "1"                     # int | 1 | retries of the clone or fetch stage
  RESTORE_CACHE_ATTEMPTS: "1"                   # int | 1 | retries of cache restore
  ARTIFACT_DOWNLOAD_ATTEMPTS: "1"               # int | 1 | retries of artifact download
  EXECUTOR_JOB_SECTION_ATTEMPTS: "1"            # int | 1 | docker: retries of a section after No Such Container
  ARTIFACT_COMPRESSION_LEVEL: fastest           # fastest, fast, default, slow, slowest | default | artifact compression; fastest stores and is what Pages range requests need
  CACHE_COMPRESSION_LEVEL: fastest              # fastest, fast, default, slow, slowest | default | cache archive compression
  CACHE_COMPRESSION_FORMAT: zipzstd             # zip, tarzstd, zipzstd | zip | zipzstd parallel per file for large caches on multi-core runners; tarzstd smallest; zip for small caches
  CACHE_REQUEST_TIMEOUT: "10"                   # int minutes | 10 | cache upload and download deadline per job
  CACHE_TRANSFER_BUFFER_SIZE: "8388608"         # int bytes | 4194304 | streaming buffer; 8 MiB when transfers cap near 20 to 30 MB/s
  CACHE_CHUNK_SIZE: "16777216"                  # int bytes | upload 16777216, download 0 | part size for multipart uploads (S3 with RoleARN, GCS, Azure) and parallel range downloads
  CACHE_CONCURRENCY: "16"                       # int | upload 16, download 0 | parallel parts or ranges; 0 or 1 sequential; memory is chunk size x this
  TRANSFER_METER_FREQUENCY: "2s"                # duration | 0 (off) | print the upload and download rate this often
  ARCHIVER_STAGING_DIR: /fast-disk/staging      # path | system temporary directory | artifact download and fastzip scratch location
  FASTZIP_ARCHIVER_CONCURRENCY: "8"             # int | CPU count | files compressed at once; memory is this x buffer size plus scratch
  FASTZIP_ARCHIVER_BUFFER_SIZE: "2097152"       # int bytes | 2097152 | in-memory buffer per file before scratch disk; 0 uses scratch only
  FASTZIP_EXTRACTOR_CONCURRENCY: "8"            # int | CPU count | files decompressed at once; no extra memory
  FF_USE_FASTZIP: "true"                        # bool | true | deprecated, removed in 19.7; false restores the legacy single-threaded zip
  FF_USE_PARALLEL_CACHE_TRANSFER: "true"        # bool | false | parallel range downloads and multipart uploads for caches; CACHE_CONCURRENCY above 1 needs it on download
  FF_USE_PARALLEL_ARTIFACT_TRANSFER: "true"     # bool | false | parallel range downloads of direct-download artifacts; the backend must serve 206
  FF_USE_DIRECT_DOWNLOAD: "true"                # bool | true | first artifact download goes to object storage when the administrator allows direct mode
  FF_USE_GIT_NATIVE_CLONE: "true"               # bool | false | native git clone for GIT_STRATEGY clone; enables GIT_CLONE_EXTRA_FLAGS
  FF_USE_GIT_BUNDLE_URIS: "true"                # bool | true | let Git fetch bundles the server offers
  FF_USE_GIT_PROACTIVE_AUTH: "true"             # bool | false | send credentials before the 401 round trip on clone and fetch
  FF_ENABLE_JOB_CLEANUP: "true"                 # bool | false | clean the project directory at job end so the next fetch starts tidy
```

```toml native-config=gitlab-runner/config.toml
# Comment: type | default | effect
[[runners]]
  environment = ["FF_USE_FASTZIP=1", "CACHE_COMPRESSION_LEVEL=fastest"]   # array of strings | unset | the same variables for every job of this runner
  use_git_packfile_uris = true             # bool | false | offload packfile downloads to object storage URIs the server offers
  pre_get_sources_script = "git config --global fetch.parallel 8"   # string | unset | Git client tuning before fetch
  output_limit = 8192                      # int kilobytes | 4096 | larger logs before truncation
  prepare_timeout = "10m"                  # duration | job timeout | fail fast when the executor cannot start
  get_sources_timeout = "15m"              # duration | job timeout | fail fast when the clone hangs
  [runners.custom_build_dir]
    enabled = true                         # bool | executor default | allows GIT_CLONE_PATH
  [runners.docker]
    pull_policy = ["if-not-present", "always"]   # string or array | always | reuse local images first; a registry mirror or the Dependency Proxy cuts pulls further
    helper_image = "registry.example.internal/gitlab-runner-helper:x86_64-v${CI_RUNNER_VERSION}"   # string | registry.gitlab.com/gitlab-org/gitlab-runner/gitlab-runner-helper:<arch>-<revision> | local mirror
    volumes = ["/cache"]                   # array of strings | unset | local cache volume between jobs on one host
  [runners.kubernetes]
    helper_memory_limit = "512Mi"          # quantity | unset | more helper memory for cache and artifact archiving
    helper_image = "registry.example.internal/gitlab-runner-helper:x86_64-v${CI_RUNNER_VERSION}"
    [[runners.kubernetes.volumes.pvc]]
      name = "build-pvc-$CI_CONCURRENT_ID" # one claim per slot keeps the worktree so GIT_STRATEGY fetch works
      mount_path = "/mnt/builds"
  [runners.cache]
    Type = "s3"
    [runners.cache.s3]
      BucketLocation = "eu-west-1"         # string | unset | same region as the runners
      RoleARN = "arn:aws:iam::123456789012:role/runner-cache"   # string | unset | multipart transfers for archives above 5 GB
      Accelerate = true                    # bool | false | Transfer Acceleration when runners are far from the bucket
  [runners.artifact]
    upload_timeout = "2h"                  # duration | 1h | large artifacts on slow links
```

Keep the legacy S3 and GCS cache adapter flags off; the default adapters use the provider SDKs. A registry mirror
(`registry:2` with `REGISTRY_PROXY_REMOTEURL`) or the GitLab Dependency Proxy serves repeated image pulls locally.

## Runner Operator on OpenShift

The Operator reconciles a `Runner` custom resource into a runner Deployment and its `config.toml`. Every `spec`
property, with the Operator release that introduced it when newer than the base. Comment:
`type | default | release | effect`.

```yaml
apiVersion: apps.gitlab.com/v1beta2
kind: Runner
metadata:
  name: dev
spec:
  gitlabUrl: https://gitlab.example.com   # string | required | all | GitLab instance URL
  token: gitlab-runner-secret             # string | required | all | Secret whose key runner-token holds the glrt- authentication token that gitlab_runner.py create saves; runner-registration-token is the deprecated key
  tags: "openshift, build"                # string | unset | all | comma-separated runner tags
  concurrent: 10                          # int | 10 | all | jobs running at once; 0 is not unlimited
  interval: 30                            # int seconds | 30 | all | check_interval between job requests
  locked: false                           # bool | false | 1.8 | lock the runner to its project
  runUntagged: false                      # bool | true without tags, else false | 1.8 | take untagged jobs
  protected: false                        # bool | false | 1.8 | run jobs on protected branches only
  cloneURL: ""                            # string | unset | all | clone_url when jobs cannot reach gitlabUrl
  env: runner-env                         # string | unset | all | ConfigMap of key-value pairs injected as runner Pod environment
  runnerImage: ""                         # string | bundled image | 1.7 | runner image override
  helperImage: ""                         # string | unset | all | helper image override
  buildImage: ""                          # string | unset | all | image for jobs that name none
  cacheType: s3                           # string | unset | all | s3, gcs or azure
  cachePath: ""                           # string | unset | all | cache path prefix
  cacheShared: true                       # bool | unset | all | share the cache between runners
  s3: {}                                  # object | unset | all | S3 cache properties; see Operator cache properties
  gcs: {}                                 # object | unset | all | GCS cache properties
  azure: {}                               # object | unset | all | Azure cache properties
  ca: runner-ca                           # string | unset | all | TLS Secret with custom CA certificates
  serviceaccount: ""                      # string | unset | all | service account of the runner Pod
  config: custom-config-toml              # string | unset | all | ConfigMap with a config.toml template under key config.toml
  shutdownTimeout: 30                     # int seconds | 30 | 1.34 | forceful shutdown deadline; 0 or lower restores 30
  logLevel: info                          # string | unset | 1.34 | debug, info, warn, error, fatal or panic
  logFormat: runner                       # string | runner | 1.34 | runner, text or json
  listenAddr: ""                          # string | unset | 1.34 | host:port of the Prometheus metrics server
  sentryDsn: ""                           # string | unset | 1.34 | report system-level errors to Sentry
  connectionMaxAge: 15m                   # duration | 15m | 1.34 | TLS keepalive connection lifetime; 0 or lower keeps it open
  podSpec: []                             # list of patches | unset | 1.23 | patches on the runner Pod template; keys name, patch or patchFile, patchType (merge, json, strategic)
  deploymentSpec: []                      # list of patches | unset | 1.40 | the same patches on the runner Deployment
```

### Operator cache properties

Credentials live in Secrets the Operator reads; the key names inside each Secret are fixed.

```yaml
spec:
  cacheType: s3
  s3:
    server: s3.example.internal:9000      # string | unset | S3 server address
    credentials: s3-cache-secret          # string | unset | Secret with keys accesskey and secretkey
    bucket: runner-cache                  # string | unset | bucket
    location: us-east-1                   # string | unset | S3 region
    insecure: false                       # bool | unset | plain HTTP
  gcs:
    credentials: gcs-cache-secret         # string | unset | Secret with keys access-id and private-key
    bucket: runner-cache                  # string | unset | bucket
    credentialsFile: keys.json            # string | unset | GCS credentials file
  azure:
    credentials: azure-cache-secret       # string | unset | Secret with keys accountName and privateKey
    container: runner-cache               # string | unset | blob container
    storageDomain: blob.core.windows.net  # string | unset | storage endpoint domain
```

### Operator custom config.toml and environment

Any `[[runners]]` key the CR does not expose goes through a configuration template: write the TOML, store it in a
ConfigMap under the key `config.toml`, and name that ConfigMap in `spec.config`. The Operator merges the
template into the generated file.

```toml native-config=gitlab-runner/config.toml
[[runners]]
  [runners.kubernetes]
    cpu_limit = "500m"
    [runners.kubernetes.volumes]
      [[runners.kubernetes.volumes.empty_dir]]
        name = "empty-dir"
        mount_path = "/path/to/empty_dir"
        medium = "Memory"
```

```bash
oc -n gitlab-runner create configmap custom-config-toml --from-file config.toml=custom-config.toml
```

The ConfigMap lives in the Runner resource's namespace, `gitlab-runner` in this example.

Four settings are not read from the template because of a known Operator issue; set them as environment
variables in the ConfigMap named by `spec.env`.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: runner-env
data:
  RUNNER_REQUEST_CONCURRENCY: "1"    # int | 1 | [[runners]] request_concurrency
  RUNNER_OUTPUT_LIMIT: "4096"        # int kilobytes | 4096 | [[runners]] output_limit
  KUBERNETES_POLL_TIMEOUT: "180"     # int seconds | 180 | [runners.kubernetes] poll_timeout
  KUBERNETES_NAMESPACE: "ci-jobs"    # string | the Runner resource's namespace | [runners.kubernetes] namespace
```

Size `spec.concurrent` from one job's CPU and memory against what the cluster can schedule; a high value only
lets the Kubernetes scheduler decide when each job Pod runs.

## Sources

GitLab Runner documentation and source at tag v19.5.0, with keys marked 19.6+ taken from the development documentation
after that tag: `docs/configuration/advanced-configuration.md`,
`docs/configuration/feature-flags.md`, `docs/configuration/speed_up_job_execution.md`,
`docs/configuration/configuring_runner_operator.md`, `docs/executors/kubernetes/_index.md`,
`docs/executors/docker_autoscaler.md`, `docs/executors/instance.md`, `docs/install/operator.md`, `common/config.go`,
`cache/cacheconfig/cacheconfig.go` and `referees/metrics.go` in `gitlab-org/gitlab-runner`. GitLab documentation at
tag v19.5.0-ee: `doc/ci/runners/configure_runners.md` in `gitlab-org/gitlab` for the job variables.
