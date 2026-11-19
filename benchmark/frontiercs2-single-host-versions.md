# FrontierCS-2 single-host v1 compatibility archive

This is the frozen compatibility-source snapshot behind a four-arm,
one-task `batch_local_counting` pilot. It follows the
[archive placement rules](README.md#archive-placement). It is an inert historical
reference: no product imports, automatic CI execution, job launch or model call
is added. Scores, submissions and usage observations are published separately in
the [Hugging Face release](https://huggingface.co/datasets/songwells/loopxbench/tree/main/public/frontiercs2-batch-local-counting-20261102-v1).

The study has one run per arm and a five-hour answering cap, with earlier final
submissions. It uses the native verifier through a local single-host compatibility
bridge, rather than the official Management/Driver deployment. Its terminal board
still records integrity qualification as false, treatment fidelity as pending for
the LoopX arms, and score countability as false. The published observations do
not establish a qualified matched study, leaderboard result or LoopX benefit.

## Source identity

The evaluated LoopX source is `e9eda527afd5fcb262ee7c199e1822b1a936cc24`.
The task export records source revision
`d247b3f15b73805e455ff93c6d043d84603ec341`; its manifest binds the task,
dataset, reference and verifier image digests. The image/source metadata is
retained, while task inputs, reference patch and evaluator source are omitted.

The [five frozen files](frontiercs2-single-host-v1/) retain the collected source
bytes and non-executable Git modes. Their SHA-256 identities are independently
pinned in [the read-only checker](check_frontiercs2_v1.py) and the HF release.
Original docstrings saying “private” describe the experiment's former transport
boundary; these selected source files are now released. The containing directory
name changes, while the native verifier wrapper retains its original basename.

| File | Historical role |
| --- | --- |
| `frontier_submit.py` | Candidate-side train, train-status, checkpoint and final client |
| `submission_bridge.py` | Per-trial HTTP capability, immutable payloads, idempotency and grader queue |
| `docker_environment.py` | Existing benchmark installer's Docker exec/upload transport, including streamed copies for Snap Docker |
| `local_runner.py` | Operator-side native verifier invocation, resource limits and deferred no-new-privileges bootstrap |
| `source-manifest.json` | Original exported image and input identities, including the non-comparable historical baseline notice |

The campaign controller, authentication homes, private configuration, raw
trajectories and verification logs are outside this source snapshot. No private
host, account or local operating paths are needed to inspect it. A later repair
must have a new version and validation; it cannot rewrite these historical bytes
or inherit their score attribution.

## Check the snapshot

From the source checkout:

```sh
uv run --extra test python benchmark/check_frontiercs2_v1.py
```

The check verifies all five files' hashes and Git executable modes, rejects extra
source files and symlinks, parses four Python modules and the JSON manifest, and
compiles the named `NNP_BOOTSTRAP` string. Bytecode-only `__pycache__` is ignored.
It imports none of the archived modules, executes no embedded program and makes
no Docker, network, task or model request. Passing means identity and syntax
checks passed; the report explicitly says `standalone_runnable: false` and
`runtime_validation: not_performed`. Other dynamically constructed programs are
outside this syntax check.

## Runtime prerequisites and authority

For an owner-authorized replay, first assemble a **new isolated experiment
workspace**. Preserve this archive and copy the selected files into that workspace.
Keep the candidate client and HTTP bridge separate from the operator-only grader.

| Requirement | Historical interface |
| --- | --- |
| Host runtime | Python 3.12, Linux Docker and the recorded native image digests |
| Candidate client | `FRONTIER_DRIVER_API_URL` is an HTTP origin on the isolated trial network; `FRONTIER_DRIVER_TOKEN_FILE` names the independently provisioned capability file |
| Final acknowledgement | The candidate client writes `/workspace/.frontier/final-submission.json`; this is a container protocol path |
| Bridge controller | An external controller supplies `Trial`, its queue, start/deadline and native result mapping; the module alone does not launch a service |
| Existing installer | A compatible checkout of the evaluated LoopX source supplies the benchmark runtime using `DockerEnvironment` |
| Native grader inputs | The manifest, `inputs/train.json` or `inputs/eval.json`, native images, submitted tar and a writable output directory, supplied by the operator |
| Reference qualification | `qualify` additionally needs the original pull receipt and reference patch; these operator-only prerequisites are not supplied |

The no-new-privileges bootstrap assumes Linux `prctl`, default Docker seccomp,
zero effective capabilities and the `docker-default` AppArmor profile. It is not
a portable sandbox. Trusted installation can run as root before `sealed=True`;
the original controller, which is not included, owns sealing, mounts, firewall,
network isolation, token distribution, grader workers, final-stop and cleanup.
Source inspection or the hash check does not qualify those external boundaries.

## Known limits

The local API is a partial compatibility implementation, not a replacement for
the official driver. It stores control state for inspection but does not rebuild
in-memory tokens, payload ownership and idempotency after restart. Train limits
and the archive-size limit are frozen experiment assumptions. Final evaluation
is deliberately unavailable through the candidate's train-status operation.

The native grader retains task-specific names, CPU-affinity defaults, output
directory assumptions and privileged host setup needs. The Docker adapter uses
blocking subprocesses inside its asynchronous interface and can print command
and stderr fragments on errors; use only synthetic or separately reviewed
inputs. A failed final-receipt write after remote acceptance also needs explicit
controller recovery. These historical limitations are preserved rather than
silently repaired in an archive publication.

This PR performs identity and syntax validation only. The separate historical
results are observations from the original pinned environment; they are not a
new end-to-end replay, portability proof or long-horizon qualification.

## 中文说明

这里保留本次单机四组实验所用的五个兼容文件原始字节及权限，便于检查原生提交
客户端、HTTP 桥接、Docker 传输和裁判调用方式。实验数据单独存入上方 HF 目录；
仓库不包含题目输入、参考补丁、裁判源码、凭据、原始轨迹或私有运行配置。

只读检查验证文件身份与语法，不运行模型或裁判。重跑仍需在新的隔离工作区准备
外部 controller、原生镜像、数据与独立授权。数据源中的完整性资格尚未完成，
LoopX 处理忠实度仍为 pending，不能把此次归档称为正式配对收益或榜单结果。
后续兼容修复应另立版本，保留这次冻结源码及历史失败。
