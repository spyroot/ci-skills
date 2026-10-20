# CI13 chain failure before the timeout correction

One installed `image_build_readiness.py chain` invocation ran on
`a18280989e28e563c3c9f6069f5de971b6fb70fc`. Its functional checksum was
`44f40793ea53812dafca4882ab3652b34720dbe6f377c56276e23e560fd2e45f`.
The caller selected `--timeout 300`; the command exited 2 after 212 seconds.

| Observation | Captured evidence |
| --- | --- |
| Nested Podman runtime passed | `RunPodmanProbe` record in `chain-receipt.json` |
| Toolbox build timed out | `status: BLOCKED`, `reason: build_wait_timeout` in the same receipt |
| Runner and consumer job were not reached | Callback records stop during toolbox build |
| Owned resources were removed | `final-cleanup-readback.json` and `harbor-absence-readback.json` |

The chain passed `min(args.timeout, DEFAULT_COMMAND_TIMEOUT)` to `K8SAuth`.
That created a service with a 30-second bound, which `wait_build()` also used.
The standalone toolbox command already passed the caller timeout unchanged.
The native Build was removed during cleanup; its individual phase timings were
not captured. This evidence does not prove a successful toolbox build, runner
manager, or consumer job. The planned standalone manager check was not run.

All three JSON files are byte-for-byte copies of captured reports. `sha256.txt`
records their checksums. The cleanup read-back confirms that the existing SCC,
ClusterRole, and runners were preserved. No shared storage was used.
