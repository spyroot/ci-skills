# PR67 installed live evidence

Captured on 2026-10-13 from a fresh installed skill after review. These are functional observations;
GitHub static and mocked-test results are reported separately in the pull request.

- Tested source: `a9ef7ff49dbed26726f327a19485a9ab801fc9d7`.
- Package checksum:
  `ff9ebce1733492ac83a6e49a5c630260367c526d604142b761ea52d27be1aef8`.
- Functional checksum (`bin/`, `lib/`, `schemas/`):
  `37a22309da7bd79cc36ca1da4549f713cec7cf0a8f36ebf128bbfc6602a9461e`.

## Capability results

The numbers identify the 37 approved command/operation cases. “PASS” describes the command result,
not the health of every object it inspected. Report links preserve native results; trace links are separate.

| # | Command / operation | Observed result | Report | Trace |
| --- | --- | --- | --- | --- |
| 1 | access_check | PASS; three providers | [result][r1] | [log](traces/01.jsonl) |
| 2 | ci-api check | PASS; GitHub + GitLab | [result][r2] | [logs](traces/02-github.jsonl) |
| 3 | ci-api get | PASS; exact repo/project | [result][r3] | [logs](traces/03-github.jsonl) |
| 4 | ci-binary-build plan | PASS; exact-commit planner | [result][r4] | Not emitted |
| 5 | ceph_cluster | PASS; 3 records, 0 errors | [result][r5] | [log](traces/05.jsonl) |
| 6 | ceph_kernel | PASS; 0 matches, 0 errors | [result][r6] | Not emitted |
| 7 | cilium_node | PASS; 1 record, 0 errors | [result][r7] | Not emitted |
| 8 | cilium_status | PASS; 1 record, 0 errors | [result][r8] | [log](traces/08.jsonl) |
| 9 | selfhosted create | PASS; runner 22 online | [result][r9] | [log](traces/09.jsonl) |
| 10 | selfhosted delete | PASS; verified absent | [result][r10] | [log](traces/10.jsonl) |
| 11 | selfhosted update | PASS; runner online | [result][r11] | [log](traces/11.jsonl) |
| 12 | event_trace | PASS; 1772 records, 0 errors | [result][r12] | [log](traces/12.jsonl) |
| 13 | gitlab_access | PASS; authenticated setup | [result][r13] | [log](traces/13.jsonl) |
| 14 | issue create-bug | APPLIED; issue 13 | [result][r14] | [log](traces/14.jsonl) |
| 15 | issue open-bug | NO_OP; same issue | [result][r15] | [log](traces/15.jsonl) |
| 16 | job get | PARTIAL; no runner reference | [result][r16] | [log](traces/16.jsonl) |
| 17 | job list | PASS; 5 records | [result][r17] | [log](traces/17.jsonl) |
| 18 | milestone adjust-time | APPLIED + NO_OP | [result][r18] | [log](traces/18.jsonl) |
| 19 | milestone create | APPLIED + NO_OP; ID 88 | [result][r19] | [log](traces/19.jsonl) |
| 20 | milestone update | APPLIED + NO_OP | [result][r20] | [log](traces/20.jsonl) |
| 21 | pipeline read | PASS; target pipeline failed | [result][r21] | [log](traces/21.jsonl) |
| 22 | runner assign | APPLIED + NO_OP; project 124 | [result][r22] | [log](traces/22.jsonl) |
| 23 | runner create | APPLIED; record 72 | [result][r23] | [log](traces/23.jsonl) |
| 24 | runner delete | APPLIED + NO_OP | [result][r24] | [log](traces/24.jsonl) |
| 25 | runner get | PASS; record 72 | [result][r25] | [log](traces/25.jsonl) |
| 26 | runner list | PASS; record 72 | [result][r26] | [log](traces/26.jsonl) |
| 27 | runner tag | APPLIED + NO_OP | [result][r27] | [log](traces/27.jsonl) |
| 28 | wiki create | APPLIED + NO_OP | [result][r28] | [log](traces/28.jsonl) |
| 29 | wiki update | APPLIED + NO_OP | [result][r29] | [log](traces/29.jsonl) |
| 30 | harbor_pull | PASS; approved digest pulled | [result][r30] | [log](traces/30.jsonl) |
| 31 | harbor_push | PASS; copied digest verified | [result][r31] | [log](traces/31.jsonl) |
| 32 | MTU verify | PASS; eno1/eno2 = 9000 | [result][r32] | [log](traces/32.jsonl) |
| 33 | reference expand | PASS; 27 nodes, 12 pointers | [result][r33] | Not emitted |
| 34 | storage_report | PASS; 8 records, 0 errors | [result][r34] | [log](traces/34.jsonl) |
| 35 | toolbox create | PASS; reused existing build | [result][r35] | [log](traces/35.jsonl) |
| 36 | toolbox delete | PASS; build/tags absent | [result][r36] | [log](traces/36.jsonl) |
| 37 | toolbox update | PASS; real build then reuse | [result][r37] | [log](traces/37.jsonl) |

- Cases 2–3 also captured [GitLab authentication](results/02-gitlab.json) and
  [project read-back](results/03-gitlab.txt), with [check](traces/02-gitlab.jsonl) and
  [get](traces/03-gitlab.jsonl) traces.
- Case 4 proves the planner, not a live build. Case 37 built `ci-skills-toolbox-pr67-1`,
  then [reused it](results/37-repeat.json); case 35 also reused that build.
- Case 16 returned `PARTIAL` for job 53151 because `runner_reference_missing` was observed.
  Case 21 successfully read failed pipeline 3256; it does not claim that pipeline passed.
- Case 23 created a GitLab runner record; `never_contacted` is expected without an executor.
  Case 9 separately created a working GitHub runner and observed it `online`.
- Case 30 first rejected two tag-form inputs during planning: [first](results/30-rejected-tag.json),
  [second](results/30-rejected-tag-2.json). The approved digest-pinned input then passed live.
- The [interface report](results/33-interface.json) also records 22 help, 22 describe,
  27 discovery, 12 pointer, and 2 installed-schema checks; no inherited `PYTHONPATH` was used.
- Repeat-operation receipts remain alongside their APPLIED counterparts in the existing receipt directories.
  Their separate logs use `-repeat.jsonl` names in [traces](traces/).

## Cleanup and capture boundaries

Issue 13, milestone 88, and the wiki page were deleted with HTTP 204, followed by HTTP 404 read-back.
Runner 72 is absent from both projects and its native get returned exit 2. The temporary GitHub runner,
Kubernetes Deployment, pull Job/Secret, toolbox BuildConfig/tags, and namespace were removed.
The MTU probe left no marked Pods. [Cleanup outputs](cleanup/) preserve the independent observations.
The Harbor source image was retained; only the created copy tag was deleted.

The [assignment-project authentication receipt](../receipts/gitlab-access-assignment.json) was captured
after assignment, so it is later read-back, not pre-write setup. The primary setup preceded mutations.
The historical `operator-laptop.json` publication receipt, kept under `../historical/` where the acceptance
checker does not evaluate it, is not current proof for this run.

## Published artifacts

[Raw SHA-256 values](raw-sha256.txt) identify the original bytes before publication transforms.
Copies use the existing receipt redaction/path-tokenization functions, with explicit tokenization of
local target, command, source-repository, and interface paths. Result trace pointers are relative links.
Traces retain their native events; readers that emitted only a completion event have no invented HTTP trace.

Full Ceph, Cilium status, event, and storage inventories remain private. Their exact status/counts appear
above, their native traces are linked, and `PRIVATE/05`, `08`, `12`, and `34` entries in the checksum file
identify the full captured reports. Raw stderr, credential files, and local execution metadata remain private.

[r1]: results/01.json
[r2]: results/02-github.json
[r3]: results/03-github.txt
[r4]: results/04.json
[r5]: raw-sha256.txt
[r6]: results/06.json
[r7]: results/07.json
[r8]: raw-sha256.txt
[r9]: results/09.json
[r10]: results/10.json
[r11]: results/11.json
[r12]: raw-sha256.txt
[r13]: ../receipts/gitlab-access.json
[r14]: ../receipts/issue-open-bug-applied.json
[r15]: ../receipts/issue-open-bug-no_op.json
[r16]: results/16.json
[r17]: results/17.json
[r18]: ../receipts/milestone-adjust-time-applied.json
[r19]: ../receipts/milestone-create-applied.json
[r20]: ../receipts/milestone-update-applied.json
[r21]: results/21.json
[r22]: ../receipts/runner-assign-applied.json
[r23]: ../receipts/runner-create-applied.json
[r24]: ../runner-lifecycle/runner-delete-applied.json
[r25]: ../runner-lifecycle/runner-get.json
[r26]: ../runner-lifecycle/runner-list.json
[r27]: ../runner-tag/runner-tag-applied.json
[r28]: ../receipts/wiki-create-applied.json
[r29]: ../receipts/wiki-update-applied.json
[r30]: results/30.json
[r31]: results/31.json
[r32]: results/32.json
[r33]: results/33-interface.json
[r34]: raw-sha256.txt
[r35]: results/35.json
[r36]: results/36.json
[r37]: results/37.json
