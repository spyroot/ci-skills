# OpenShift SCC access

Use this reference to select an existing Security Context Constraint (SCC) or inspect a permission/admission failure.
The commands below are read-only diagnostics. Replace `KUBECONFIG_PATH` and `CONTEXT` with the kubeconfig path
and context selected in your [target file](access.md); native `oc` does not read that file itself.
They do not prepare external grants for the [Podman probe](podman-readiness.md).

## Discover and inspect

`SCC_NAME` is an existing name returned by the first command:

```bash
# List the SCC names and settings exposed by this cluster.
oc --kubeconfig KUBECONFIG_PATH --context CONTEXT get scc
# Inspect the selected SCC, including its directly configured users/groups.
oc --kubeconfig KUBECONFIG_PATH --context CONTEXT describe scc SCC_NAME
```

See [OpenShift 4.22 SCC command reference][scc], sections 17.7.1–17.7.2.

## Read permission

Replace `NAMESPACE` with the workload namespace and `SERVICE_ACCOUNT` with its Pod's `spec.serviceAccountName`.

```bash
# Query RBAC permission for this exact namespaced account.
oc --kubeconfig KUBECONFIG_PATH --context CONTEXT auth can-i use scc/SCC_NAME \
  --as system:serviceaccount:NAMESPACE:SERVICE_ACCOUNT --namespace NAMESPACE
```

`can-i` and `who-can` examine RBAC grants; they omit direct SCC `users`/`groups`. Conversely, `describe scc`
shows those direct assignments but omits RBAC grants. Read both when diagnosing access; a negative RBAC answer
alone does not describe every SCC grant. This distinction is documented in [OpenShift 4.22, section 17.3][scc].

## Read the admitted SCC

For an existing or retained workload, replace `POD_NAME` with its Pod name in `NAMESPACE`.
OpenShift records the admitted constraint in `openshift.io/scc`; permission alone does not identify the admitted SCC.

```bash
# Read the SCC actually selected for this Pod while it exists.
oc --kubeconfig KUBECONFIG_PATH --context CONTEXT get pod POD_NAME --namespace NAMESPACE \
  -o jsonpath='{.metadata.annotations.openshift\.io\/scc}{"\n"}'
```

See [OpenShift 4.22, section 17.5][scc]. The Podman probe cleans its Pod before returning, so this read is not a
post-probe step. The probe's current report does not include the annotation.

## Compose the existing callback

`SCCAccessAction` in `lib/python/core/kubernetes/scc_access.py` accepts `constraint` and `service_account`;
`EnsureSCCAccess` obtains the namespace from the shared `RunScope` opened by the preceding callback.
Another workflow can supply different existing names without changing the callback. The Podman consumer supplies
`privileged` and `default`; it exposes no `--scc` or `--service-account` CLI options.

The callback checks RBAC access using `can-i`. It preserves existing authorization or creates one run-owned
namespaced RoleBinding to `system:openshift:scc:<constraint>`, verifies it and cleans only that binding.
It creates no SCC or ServiceAccount. The calling identity needs read access to the selected SCC and ClusterRole;
when RBAC access is missing, it also needs namespaced RoleBinding create/delete and exact ClusterRole bind rights.
Only absence of the OpenShift SCC API is not applicable; missing SCC/ClusterRole or denied permission preparation
stops the downstream workload.

[scc]: https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/authentication_and_authorization/managing-pod-security-policies
