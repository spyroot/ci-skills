# Priorities

- audit and polish phase documentation, naming consistency, and done or not done classification under standards;
  identify missing implementations.
- adjust contribution_guide.md and README.md to reflect the new API; polish the README as a user-facing document.
- finish Claude and Codex hooks implementation.
- create, document, and polish the reference layout; add scraper tooling to collect and structure references.
- add a capability for ci-skills to deploy a runner and hook it to a GitHub project in one go.
- introduce a phase to collect the latest reference and build skill references: <https://docs.redhat.com/en/documentation/red_hat_openshift_data_foundation/4.11/html/managing_and_allocating_storage_resources/creating-exports-using-nfs_rhodf>
- introduce a reference for Isovalent.
- port code from ~/dev/galileo related to Red Hat Ignition so ci-skills can build an Ignition ISO.
- add more variations for combined or chained tools. For example, ci-skills can use GitLab Agents
  to attach Kubernetes:
  <https://docs.gitlab.com/user/clusters/agent/>
  <https://docs.gitlab.com/charts/installation/cloud/>
- add a capability for <https://docs.gitlab.com/operations/integrated_error_tracking/> and create universal standards
  for how the skill helps users create instrumentation boilerplate templates for consumer pipelines and jobs.
- add a template for GitLab tagging and create reusable tagging boilerplate.
- add capabilities for GitLab snippets and artifacts, and integration with Buildkite.
- add a capability to integrate with Linear and reuse the pattern from PR coordination for consistency in tags and information.
- add a ci-skills capability to automatically optimize toolbox parallelization for a given target cluster or GitHub Actions.
- add a capability to read the NVIDIA Operator: <https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/index.html>
- create an aggregate view for NVIDIA that includes driver, firmware, and GPU generation capabilities; propose more.
- add a capability to aggregate OpenShift routes, their exposure, machine specs, and deployments.
