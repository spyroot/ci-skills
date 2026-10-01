# Event trace A/B benchmark

This harness measures the first GAL-19 optimization candidate: replacing two
whole-cluster EventList reads with one preferred API read and a compatibility
fallback. It does not implement that optimization.

## Hypothesis and boundary

The baseline collector reads both `core/v1` Events and `events.k8s.io/v1`
Events. If the candidate reads `events.k8s.io/v1` first and calls the core API
only when the preferred API is unavailable, the event collection boundary
should issue fewer Kubernetes client calls and should use less wall time, CPU,
and memory while returning the same semantic event records.

The timed boundary is `collect_events()`. A live access preflight runs once for
each exact source SHA before timing and is excluded from the samples. Both
variants use the same target file, credential sources, kubeconfig bytes, time
window, Python runtime, execution Pod, and environment.

## Execution contract

Run this only in an authorized Linux Kubernetes Job or Kubernetes-backed CI
runner. The harness refuses a laptop or ordinary hosted runner by requiring the
in-cluster `KUBERNETES_SERVICE_HOST` environment. Prepare two clean, detached
checkouts and mount the operator-selected target and credentials into the same
Pod.

```bash
python /workspace/harness/benchmarks/event_trace_ab.py \
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
  --json-out /artifacts/event-trace-ab.json \
  --markdown-out /artifacts/event-trace-ab.md
```

The worker puts a temporary counting wrapper in front of the already resolved
`kubectl` path. The wrapper records one line per invocation and never records
arguments, output, environment, or credentials. Choose a closed time window
that will not change during the alternating A/B runs. The harness runs A/B for
the first pair, B/A for the second, and repeats that order to reduce time-order
bias. It never emits event records, token values, raw credential references,
kubeconfig contents, or provider stderr.

## Acceptance

The benchmark result is `PASS` only when all of these hold:

- both clean skill trees prove their exact Git SHA;
- both live access preflights pass and resolve the same target, credentials,
  and kubeconfig content digests;
- at least five measured samples per variant complete with `PASS` and no
  collector errors;
- every sample within each variant has one stable semantic SHA-256;
- baseline and candidate record counts and semantic SHA-256 values match.

Performance acceptance thresholds are operator inputs, not GAL-19 product
policy. Add any applicable thresholds to the command when the review authority
has selected them:

```bash
  --minimum-improvement-percent "$MINIMUM_MEDIAN_IMPROVEMENT" \
  --minimum-faster-pairs "$MINIMUM_FASTER_PAIRS" \
  --maximum-p95-regression-percent "$MAXIMUM_P95_REGRESSION"
```

When no thresholds are supplied, the evidence reports measured direction,
paired improvements, variance, p95 change, and Kubernetes call reduction with
performance acceptance `NOT_CONFIGURED`.

The generated Markdown is the PR evidence table. Its fixed columns are:

| Field | Baseline | Candidate | Result |
| --- | --- | --- | --- |
| Exact SHA | Full commit SHA | Full commit SHA | Both pinned |
| Samples | Measured count | Measured count | Warmups excluded |
| Wall p50 | Seconds | Seconds | Percent improvement |
| Wall p95 | Seconds | Seconds | Percent improvement |
| Wall CV | Percent | Percent | Variance read-back |
| Max process RSS p50 | KiB | KiB | Percent improvement |
| Record counts | Stable set | Stable set | Equality gate |
| Semantic SHA-256 | Stable digest | Stable digest | Equality gate |
| Collector errors | Stable set | Stable set | Zero-error gate |
| Kubernetes calls | Stable count | Stable count | Reduction gate |
| Faster sample pairs | Not applicable | Observed wins | Required wins |
| Correctness gate | Not applicable | Not applicable | `PASS` or `BLOCKED` |
| Performance acceptance | N/A | N/A | `PASS/BLOCKED/NOT_CONFIGURED` |
| Overall | Not applicable | Not applicable | `PASS` or `BLOCKED` |

The JSON artifact contains the same summary plus raw, bounded sample metrics.
It is evidence for only the recorded executor, target, time window, and SHA
pair. A synthetic provider benchmark cannot replace this run.

Use `--dry-run` with the same arguments to write a `DRY_RUN` plan without
reading source trees, credentials, providers, or Kubernetes.
