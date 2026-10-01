# Event trace A/B benchmark

This harness measures the first GAL-19 optimization candidate: replacing two
whole-cluster EventList reads with one preferred API read and a compatibility
fallback. It does not implement that optimization.

## Hypothesis and boundary

The baseline collector reads both `core/v1` Events and `events.k8s.io/v1`
Events. If the candidate reads `events.k8s.io/v1` first and calls the core API
only when the preferred API is unavailable, the event collection boundary
should issue fewer Kubernetes client calls and should use less wall time and CPU
while returning the same semantic event records.

The timed boundary is `collect_events()`. A live access preflight runs once for
each exact source SHA before timing and is excluded from the samples. Both
variants use the same target file, credential sources, kubeconfig bytes, time
window, Python runtime, execution Pod, and environment. Every measured sample
must read back the same target, credential-source, kubeconfig, source, and
harness identities recorded by its preflight.

## Execution contract

Run this only in an authorized Linux Kubernetes Job or Kubernetes-backed CI
runner. The harness refuses a laptop or ordinary hosted runner by requiring the
in-cluster `KUBERNETES_SERVICE_HOST` environment. Prepare two clean, detached
checkouts and mount the operator-selected target and credentials into the same
Pod.

```bash
python /workspace/harness/benchmarks/event_trace_ab.py \
  --harness-sha "$HARNESS_SHA" \
  --baseline-root /workspace/baseline \
  --baseline-sha "$BASELINE_SHA" \
  --candidate-root /workspace/candidate \
  --candidate-sha "$CANDIDATE_SHA" \
  --target /run/ci-skills/target.toml \
  --executor-label "$AUTHORIZED_EXECUTOR_LABEL" \
  --from "$FIXED_FROM_RFC3339" \
  --to "$FIXED_TO_RFC3339" \
  --warmups 1 \
  --samples 7 \
  --sample-timeout-seconds 300 \
  --total-timeout-seconds 900 \
  --minimum-improvement-percent "$MINIMUM_MEDIAN_IMPROVEMENT" \
  --json-out /artifacts/event-trace-ab.json \
  --markdown-out /artifacts/event-trace-ab.md
```

The worker puts a temporary counting wrapper in front of the already resolved
`kubectl` path. The wrapper records one line per invocation and never records
arguments, output, environment, or credentials. Choose a closed time window
that will not change during the alternating A/B runs. The harness runs A/B for
the first pair, B/A for the second, and repeats that order to reduce time-order
bias. The harness subtree, worker, baseline skill, and candidate skill must be
clean and match their full SHAs. It never emits event records, token values,
raw credential references, kubeconfig contents, or provider stderr.

The run is bounded to 1-3 warmups, 5-20 measured pairs, a maximum 300-second
worker timeout, and a maximum 1,800-second total deadline. The default plan is
1 warmup, 7 pairs, a 300-second worker timeout, and a 900-second total deadline.
Those limits cap the default at 18 live operations and every accepted plan at
48 live operations.

## Acceptance

The benchmark result is `PASS` only when all of these hold:

- both clean skill trees prove their exact Git SHA;
- the clean benchmark harness proves its exact Git SHA and content digest;
- both live access preflights pass and resolve the same target, credentials,
  and kubeconfig content digests;
- every warmup and measured sample matches its variant preflight identity;
- at least five measured samples per variant complete with `PASS` and no
  collector errors;
- every sample within each variant has one stable semantic SHA-256;
- baseline and candidate record counts and semantic SHA-256 values match;
- the candidate has a faster median and a stable Kubernetes-call reduction;
- at least one explicit performance threshold is configured and passes.

Performance acceptance thresholds are operator inputs. At least one is required
before the overall result can be `PASS`:

```bash
  --minimum-improvement-percent "$MINIMUM_MEDIAN_IMPROVEMENT" \
  --minimum-faster-pairs "$MINIMUM_FASTER_PAIRS" \
  --maximum-p95-regression-percent "$MAXIMUM_P95_REGRESSION"
```

When no thresholds are supplied, the evidence reports the measurements but
blocks performance acceptance. A slower candidate blocks even if a permissive
p95 threshold was selected.

The generated Markdown is the PR evidence table. Its fixed columns are:

| Field | Baseline | Candidate | Result |
| --- | --- | --- | --- |
| Harness exact SHA | Full commit SHA | Full commit SHA | Harness pinned |
| Harness tree SHA-256 | Content digest | Content digest | Harness identity |
| Source exact SHA | Full commit SHA | Full commit SHA | Both pinned |
| Source tree SHA-256 | Content digest | Content digest | Source identity |
| Samples | Measured count | Measured count | Warmups excluded |
| Wall p50 | Seconds | Seconds | Percent improvement |
| Wall p95 | Seconds | Seconds | Percent improvement |
| Wall CV | Percent | Percent | Variance read-back |
| Record counts | Stable set | Stable set | Equality gate |
| Semantic SHA-256 | Stable digest | Stable digest | Equality gate |
| Collector errors | Stable set | Stable set | Zero-error gate |
| Kubernetes calls | Stable count | Stable count | Reduction gate |
| Faster sample pairs | Not applicable | Observed wins | Required wins |
| Correctness gate | Not applicable | Not applicable | `PASS` or `BLOCKED` |
| Performance acceptance | N/A | N/A | `PASS` or `BLOCKED` |
| Overall | Not applicable | Not applicable | `PASS` or `BLOCKED` |

The JSON artifact contains the same summary plus raw, bounded sample metrics.
It is evidence for only the recorded executor, target, time window, and SHA
pair. A synthetic provider benchmark cannot replace this run.

Use `--dry-run` with the same arguments to write a `DRY_RUN` plan without
reading source trees, credentials, providers, or Kubernetes.

`event_trace_evidence.py` owns response validation and acceptance calculations;
`source_identity.py` owns exact-subtree provenance. The normal CI test step
discovers `tests/test_event_trace_benchmark.py`, which covers malformed worker
responses, identity drift, thresholds, alternating order, provenance, and run
limits without contacting a cluster.
