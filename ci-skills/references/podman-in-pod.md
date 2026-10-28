# Podman inside a Pod

The [readiness probe](podman-readiness.md) proves one arrangement: a privileged Job with native `overlay`
storage and `cgroupfs`. The [chain](image-build-chain.md) publishes a toolbox whose runner builds images with the
same nested Podman. This reference lists the inputs that decide whether such a build works inside a Pod and how
it pushes, as the man pages state them at Podman v6.1.4 with the common v0.69.2 and storage v1.64.2 libraries
that release pins. Values are examples; the comment on each key is its type, default and meaning.

Configuration files live in the toolbox image or in a mounted ConfigMap. Storage selects one `storage.conf`: the
first that exists of `$XDG_CONFIG_HOME/containers/storage.conf` (`$HOME/.config/containers/storage.conf`),
`/etc/containers/storage.conf` and `/usr/share/containers/storage.conf`; a user or `/etc` file replaces the lower
file whole and inherits nothing from it, so a short override must repeat `driver`, `graphroot`, `runroot` and the
overlay options it relies on. Drop-ins under `storage.conf.d/` (and the `rootful`/`rootless` variants the man page
lists) then load in file-name order over that one file. `containers.conf` follows the same rule with its own
paths and `containers.conf.d/`; `CONTAINERS_CONF` replaces that set, `CONTAINERS_CONF_OVERRIDE` is loaded last, and
`--module PATH` adds a file before any sub-command.

## Storage configuration

Every key sits under `[storage]`; the file is TOML. `driver`, `graphroot`, `runroot` and the overlay options
decide whether image layers can be mounted at all inside the Pod.

```conf
# /etc/containers/storage.conf
[storage]
driver = "overlay"                     # string; "overlay"|"vfs"|"btrfs"|"zfs"; rootless: "overlay", "vfs", "btrfs";
                                       # unset = driver_priority, then the first known driver that works
driver_priority = ["overlay", "vfs"]   # list; tried in order when driver is unset and the store is new
graphroot = "/var/lib/containers/storage"   # string; default /var/lib/containers/storage, rootless
                                            # $XDG_DATA_HOME or $HOME/.local/share/containers/storage
runroot = "/run/containers/storage"    # string; default /run/containers/storage, rootless $XDG_RUNTIME_DIR/containers
                                       # or /tmp/storage-run-$UID/containers; keep it on tmpfs
imagestore = ""                        # string; default = graphroot; separate store for newly pulled images
transient_store = false                # bool; default false; container metadata in runroot only, lost on reboot

[storage.options]
additionalimagestores = []             # list of paths; read-only image stores shared into this one
root-auto-userns-user = ""             # string; user whose /etc/subuid and /etc/subgid ranges back userns=auto;
                                       # ignored rootless
auto-userns-min-size = 1024            # int; default 1024; smallest automatic user namespace
auto-userns-max-size = 65536           # int; default 65536; largest automatic user namespace

[storage.options.overlay]
mount_program = "/usr/bin/fuse-overlayfs"   # string; default "" = kernel overlay; required where the kernel refuses
                                            # an unprivileged overlay mount, or the backing filesystem is btrfs,
                                            # zfs, overlay or ecryptfs
mountopt = "nodev"                     # string; comma-separated mount(8) options; the shipped file sets "nodev"
force_mask = ""                        # string; ""|"shared" (0755)|"private" (0700)|octal; default ""; needs
                                       # mount_program; experimental
ignore_chown_errors = "false"          # string bool "true"|"false"; default "false"; a single-UID user namespace
                                       # can use multi-UID images, with their UIDs squashed to one
skip_mount_home = "false"              # string bool; default "false"; skip the private bind mount of the store
size = ""                              # string; largest read/write layer, <number>[b|k|m|g]; needs XFS pquota
inodes = ""                            # string; most inodes per read/write layer
use_composefs = "false"                # string bool; default "false"; experimental
sync = "none"                          # "none"|"filesystem"; default "none"; syncfs before a layer counts as present

[storage.options.vfs]
ignore_chown_errors = "false"          # string bool; same meaning as for overlay
```

The same choices travel as global flags, written before the sub-command: `--storage-driver DRIVER`,
`--root PATH`, `--runroot PATH`, `--imagestore PATH`, `--transient-store` and `--storage-opt DRIVER.KEY=VALUE`,
such as `podman --storage-opt overlay.mount_program=/usr/bin/fuse-overlayfs build ...`. The overlay keys accepted
there are `mount_program`, `mountopt`, `size`, `inodes`, `skip_mount_home`, `ignore_chown_errors`, `force_mask`,
`use_composefs`, `sync`, `imagestore`, `additionalimagestore` and `additionallayerstore`. `STORAGE_DRIVER` and a
comma-separated `STORAGE_OPTS` do the same from the environment. `--root` and `--storage-driver` discard the file's
`storage.options` and `STORAGE_OPTS`, so every option is then passed again with `--storage-opt`.

## Which storage arrangement works

Three arrangements, from Podman's troubleshooting notes on Podman inside a container (entries 13 and 42) and the
storage man page:

- Mount a volume at `/var/lib/containers/storage` (at least that directory) and keep the native kernel `overlay`.
  With the Pod's own rootfs as the store, a nested overlay falls back to `fuse-overlayfs`, which makes `COPY`, `ADD`
  and I/O-heavy `RUN` lines slow or hanging. The probe does this: an `emptyDir` sized by `--storage-size` at
  `/var/lib/containers`, another at `/run/containers`, then `podman --root /var/lib/containers/storage
  --runroot /run/containers/storage --storage-driver overlay --storage-opt= --cgroup-manager cgroupfs`. The kernel
  overlay needs the mount permitted for the Pod's user, which its privileged Job has.
- Set `driver = "vfs"` when the Pod spec cannot add that volume, as in a fixed CI job. Every layer is copied, so it
  uses more space, and it works without overlay, fuse or extra capabilities; the notes rate it slower than the
  volume route and well ahead of `fuse-overlayfs`.
- Keep `overlay` with `mount_program = "/usr/bin/fuse-overlayfs"` where the kernel refuses the mount and the slower
  writes are acceptable. The `fuse` module must be loaded on the node and `/dev/fuse` present in the container
  (`fuse: device not found` otherwise); a nested container reaches it with `--device /dev/fuse`.

Sharing the node's `/var/lib/containers/storage` into the Pod also requires its `/run/libpod` and
`/run/containers/storage`; without them Podman treats the missing run files as a reboot and resets container state.

## containers.conf keys that matter in a Pod

A Pod has no systemd and usually no journal, and its namespaces are already the Pod's. These `[containers]` and
`[engine]` keys select what Podman creates for each nested container.

```conf
# /etc/containers/containers.conf
[containers]
default_capabilities = [               # list; the default set below; add with --cap-add per build
  "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "KILL", "NET_BIND_SERVICE",
  "SETFCAP", "SETGID", "SETPCAP", "SETUID", "SYS_CHROOT",
]
cgroups = "disabled"                   # "enabled"|"disabled"|"no-conmon"; default "enabled"; disabled inherits the
                                       # Pod's cgroup instead of creating one
cgroupns = "host"                      # "private"|"host"; default "private"
netns = "host"                         # same values as --network; default "" = bridge as root, pasta rootless
userns = "host"                        # "host"|"private"|"auto"|"keep-id"|"no-map"; default "host"
ipcns = "host"                         # "host"|"none"|"private"|"shareable"; default "shareable"
pidns = "private"                      # "private"|"host"; default "private"
utsns = "host"                         # "private"|"host"; default "private"
label = false                          # bool; default true; SELinux separation; ignored where SELinux is off
seccomp_profile = "/usr/share/containers/seccomp.json"   # path; default
apparmor_profile = "container-default" # string; default
no_hosts = false                       # bool; default false; true leaves /etc/hosts to the image
pids_limit = 1024                      # int; default 1024; 0 = no limit
shm_size = "65536k"                    # string; default "65536k"
default_sysctls = []                   # list of "name=value"
default_ulimits = []                   # list of "name=soft:hard"
volumes = []                           # list of "host:container:options"; the Podman image's rootless file
                                       # sets ["/proc:/proc"]
mounts = []                            # list of "type=bind,source=HOST,destination=CONTAINER,ro"
log_driver = "k8s-file"                # "k8s-file"|"journald"|"none"|"passthrough"; default journald when the
                                       # journal is writable, else k8s-file
env = ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]   # list; container environment

[engine]
cgroup_manager = "cgroupfs"            # "systemd"|"cgroupfs"; default "systemd", which needs a reachable systemd
events_logger = "file"                 # "file"|"journald"|"none"; default "journald" on systemd hosts
runtime = "crun"                       # string; a member of [engine.runtimes]; default search crun, runc, runj,
                                       # kata, runsc, ocijail
image_default_transport = "docker://"  # string; default "docker://"
image_parallel_copies = 0              # int; default 0 = the containers/image default of 6 layers at once
compression_format = "gzip"            # "gzip"|"zstd"|"zstd:chunked"; default "gzip"; used by push
compression_level = 5                  # int; gzip 1 to 9, default 5; zstd 1 to 20, default 3
retry = 3                              # int; default 3; pull and push retries
retry_delay = ""                       # duration; default "" = 2 s, then exponential backoff
pull_policy = "missing"                # "always"|"missing"|"never"; default "missing"
tmp_dir = "/run/libpod"                # path; default "/run/libpod"; per-boot state, keep it on tmpfs
helper_binaries_dir = ["/usr/libexec/podman", "/usr/lib/podman"]   # list; where netavark, aardvark-dns,
                                                                    # pasta and catatonit are searched

[network]
network_backend = "netavark"           # "netavark"; "" means netavark
default_rootless_network_cmd = "pasta" # "pasta"; the rootless network program
```

The Podman project's own container image ships this profile, which is the known-working baseline for the keys
above: `netns`, `userns`, `ipcns`, `utsns` and `cgroupns` all `"host"`, `cgroups = "disabled"`,
`log_driver = "k8s-file"`, `cgroup_manager = "cgroupfs"`, `events_logger = "file"`, `runtime = "crun"`, the
environment `BUILDAH_ISOLATION=chroot`, `mount_program` enabled with `mountopt = "nodev,fsync=0"` in
`storage.conf`, and volumes at `/var/lib/containers` and `/home/podman/.local/share/containers`.

## podman build flags

`--storage-opt` and the other storage flags are global and precede `build`. The flags below apply to the build
itself; most of them act on `RUN` instructions. Capitalised words are placeholders.

```bash
build=(
  --isolation chroot                   # "oci"|"rootless"|"chroot"; default oci, rootless for an unprivileged user;
                                       # chroot is an internal chroot-style wrapper instead of the OCI runtime;
                                       # BUILDAH_ISOLATION overrides
  --layers                             # bool; default true; cache each instruction as an image; BUILDAH_LAYERS
  --network host                       # "none"|"host"|"ns:PATH"|"private" (default)|NETWORK|"pasta[:OPTIONS]"
                                       # (rootless default); the network of RUN
  --userns host                        # ""|"container" (new user namespace)|"host" (reuse Podman's own)|PATH
  --userns-uid-map 0:100000:65536      # triples CONTAINER-UID:HOST-UID:COUNT; overrides remap-uids; rootless maps
                                       # are relative to the user namespace
  --userns-gid-map 0:100000:65536      # triples CONTAINER-GID:HOST-GID:COUNT
  --userns-uid-map-user USER           # take the UID map from the /etc/subuid entries of USER
  --userns-gid-map-group GROUP         # take the GID map from the /etc/subgid entries of GROUP
  --security-opt label=disable         # label=disable|label=user:U|role:R|type:T|level:L|filetype:T;
                                       # seccomp=unconfined|FILE.json; apparmor=unconfined|PROFILE; no-new-privileges
  --cap-add CAP_SYS_ADMIN              # add to the RUN capability set; repeatable
  --cap-drop CAP_NET_RAW               # remove from it; a capability in both is dropped; granted by default: CAP_CHOWN,
                                       # DAC_OVERRIDE, FOWNER, FSETID, KILL, NET_BIND_SERVICE, SETFCAP, SETGID,
                                       # SETPCAP, SETUID
  --device /dev/fuse                   # HOST-DEVICE[:CONTAINER-DEVICE][:rwm]; symlinks are resolved; Podman loads
                                       # the fuse module for /dev/fuse when it can; rootless bind-mounts the node
  --cgroupns host                      # ""|"private"|"host"; the cgroup namespace of RUN
  --pull missing                       # "always"|"missing" (default)|"never"|"newer"; base image policy
  --authfile /run/secrets/auth.json    # path; default $XDG_RUNTIME_DIR/containers/auth.json, then
                                       # $HOME/.docker/config.json; REGISTRY_AUTH_FILE overrides
  --tls-verify=true                    # bool; default true; false skips certificate verification; unset honours
                                       # the insecure list in registries.conf
  --cert-dir /etc/containers/certs.d   # path to *.crt, *.cert and *.key; default /etc/containers/certs.d
  --creds USER:PASSWORD                # credentials for the registries the build contacts, not for mirrors or
                                       # rewritten registries; --authfile is the usual route
  --format oci                         # "oci" (default)|"docker"; manifest and config format; BUILDAH_FORMAT
  --file Containerfile                 # path or URL; "-" reads stdin; repeatable, FROM honoured from the last
  --tag registry.example.internal/toolbox/runner:v1   # image name; "localhost/" is prepended without a registry
  --target STAGE                       # stop after this build stage
  --build-arg KEY=VALUE                # repeatable; --build-arg-file argfile.conf reads lines of KEY=VALUE
  --no-cache                           # ignore cached layers; --cache-ttl 0 means the same
  --cache-from REPOSITORY              # pull cache images from here; only with --layers
  --cache-to REPOSITORY                # push cache images there; only with --layers
  --cache-ttl 1h                       # only cache images younger than this
  --jobs 1                             # int; concurrent stages; 0 = unlimited; above 1 reads stdin from /dev/null
  --retry 3                            # int; default 3; pull and push retries
  --retry-delay 2s                     # duration; default 2 s with exponential backoff when unset
  --squash                             # squash the new layers into one; --squash-all includes the base layers
  --volume /cache:/cache:O             # HOST:CONTAINER[:ro|rw,z|Z|O,U,shared|slave|private] for RUN; O is rootful
  --secret id=NAME,src=FILE            # /run/secrets/NAME for RUN --mount=type=secret; absent from the image
  --ulimit nofile=65536:65536          # TYPE=SOFT[:HARD] for RUN; repeatable
  --shm-size 64m                       # /dev/shm of RUN; default 64m; 0 unlimited
  --memory 4g                          # RUN memory limit; unavailable on cgroup v1 rootless
  --add-host NAME:IP                   # /etc/hosts entry for RUN; conflicts with --no-hosts
  --dns 192.0.2.53                     # resolver for RUN; "none" keeps the image's resolv.conf
  --disable-compression=true           # bool; default true; layers stay uncompressed in local storage
  --iidfile /tmp/iid                   # write the image id here; --logfile FILE captures the build log
  --manifest NAME                      # add the image to a manifest list; with --platform OS/ARCH[/VARIANT],... for
                                       # several platforms, RUN needs emulation
)
podman build "${build[@]}" .
```

## podman push flags

`podman push IMAGE DESTINATION` accepts every transport of `containers-transports(5)`; without a transport the
destination is a registry (`docker://`). `dir:PATH`, `oci-archive:PATH` and `docker-archive:PATH` write locally.

```bash
push=(
  --authfile /run/secrets/auth.json    # path; as for build
  --tls-verify=true                    # bool; default true
  --cert-dir /etc/containers/certs.d   # path; default /etc/containers/certs.d
  --creds USER:PASSWORD                # registry credentials; --authfile is the usual route
  --digestfile /tmp/digest             # write the pushed manifest digest to this file
  --format oci                         # "oci"|"v2s2"|"v2s1"; manifest type at the destination; unset follows
                                       # [engine] image_default_format, whose default matches the source image
  --compression-format gzip            # "gzip" (default)|"zstd"|"zstd:chunked"; containers.conf compression_format
                                       # changes the default; zstd:chunked becomes zstd with encryption
  --compression-level 5                # int; gzip 1 to 9, default 5; zstd 1 to 20, default 3
  --force-compression                  # re-compress even when the destination holds another variant; default true
                                       # when --compression-format is given
  --retry 3                            # int; default 3
  --retry-delay 2s                     # duration; default 2 s with exponential backoff when unset
  --remove-signatures                  # drop signatures carried by the image
  --sign-by-sigstore PARAMS.yaml       # sigstore signing parameter file; also --sign-by-sigstore-private-key PATH,
                                       # --sign-by FINGERPRINT (GPG) and --sign-passphrase-file PATH
  --encryption-key jwe:/run/secrets/key.pem   # PROTOCOL:FILE, jwe, pgp or pkcs7; --encrypt-layer N selects layers
  --quiet                              # suppress progress output
)
podman push "${push[@]}" registry.example.internal/toolbox/runner:v1 \
  docker://registry.example.internal/toolbox/runner:v1
```

## Rootless prerequisites

A build by an unprivileged user inside the Pod needs these in place:

- Subordinate ids. `/etc/subuid` and `/etc/subgid` hold one `USERNAME:START:COUNT` line per user, with ranges that
  do not overlap; `usermod --add-subuids 100000-165535 --add-subgids 100000-165535 USER` writes them. The
  `newuidmap` and `newgidmap` helpers from `shadow-utils` apply the map; the Podman image restores their file
  capabilities with `rpm --setcaps shadow-utils` and writes `root:1:65535`, `podman:1:999` and `podman:1001:64535`.
  After a change, `podman system migrate` as that user restarts its pause process.
- Writable rootless stores: `graphroot` at `$HOME/.local/share/containers/storage` and `runroot` under
  `$XDG_RUNTIME_DIR/containers`, both owned by the build user.
- A store the user can mount: the kernel overlay where it is permitted; otherwise `driver = "vfs"`, or
  `mount_program` with `fuse-overlayfs` and `/dev/fuse` in the container when its slower writes are acceptable.
- cgroups: Podman's `--cgroup-manager` page says `cgroupfs` does not work rootless and container cgroups then go
  unused; Buildah's page limits that to cgroup v1. On cgroup v2 with a cgroup subtree the Pod's user can write,
  `cgroupfs` works; without such delegation, and with no systemd session in the Pod, `cgroups = "disabled"` keeps
  nested containers in the Pod's cgroup.
- Network for `RUN`: `pasta` from `helper_binaries_dir`, or `--network host` or `--network none`.
- `--isolation chroot` runs `RUN` through the internal chroot-style wrapper instead of the OCI runtime;
  `--userns-*-map` values given by a rootless user are relative to its user namespace, not the node.

## Sources

Podman v6.1.4: `docs/source/markdown/podman.1.md`, `podman-build.1.md.in`, `podman-push.1.md.in` and
`options/*.md`; `docs/tutorials/rootless_tutorial.md`; `troubleshooting.md` entries 13, 24 and 42; `go.mod` for
the library versions. Tags `common/v0.69.2` and `storage/v1.64.2` of the `podman-container-tools/container-libs`
repository, where `go.podman.io/common` and `go.podman.io/storage` live: `common/docs/containers.conf.5.md`,
`common/docs/containers-config.5.md`, `common/pkg/config/containers.conf`, `storage/docs/containers-storage.conf.5.md`,
`storage/storage.conf` and `storage/drivers/overlay/overlay.go` (option keys). The Podman
container image profile: `podman/containers.conf`, `podman-containers.conf` and `Containerfile` in
`containers/image_build` at commit `b86020d8c306c4724780adfb6fc492d0a2d9cc48`.
