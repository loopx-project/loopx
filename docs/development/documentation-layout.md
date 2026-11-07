# Documentation Layout And Migration Policy

LoopX documentation serves several audiences: people trying the product,
operators running long-lived goals, contributors changing the control plane,
and maintainers inspecting protocols or research evidence. This policy keeps
those paths distinct without hiding useful material or breaking stable links
for cosmetic reasons.

## Target Information Architecture

| Path | Owns | Does not own |
| --- | --- | --- |
| `docs/guides/` | Task-oriented onboarding and operator walkthroughs | Product strategy or machine contracts |
| `docs/concepts/` | Explanations of durable LoopX concepts and mental models | Step-by-step operations |
| `docs/operations/` | Goal, todo, cadence, attention, and authority workflows | Provider-specific implementation details |
| `docs/architecture/` | System boundaries, design decisions, and RFCs | Current CLI reference |
| `docs/product/` | Product direction, runtime experiences, surfaces, and use cases | Protocol definitions |
| `docs/integrations/` | Runtime, host, collaboration, and external-system adapters | Core control-plane semantics |
| `docs/reference/contracts/` | Stable human-readable contracts | Exploratory proposals |
| `docs/reference/protocols/` | Versioned, implementation-oriented protocols | Narrative product direction |
| `docs/development/` | Contributor workflows, testing, and repository policy | End-user onboarding |
| `docs/showcases/` | Public-safe cases and reproducible demonstrations | Raw private evidence |
| `docs/research/` | Public research, benchmark evidence, and route packets | Stable first-line documentation |
| `docs/archive/` | Superseded or dated records kept for historical value | Active guidance |

The `docs/` root is a compatibility surface, not the default destination for
new files. It should contain the documentation home and a small set of stable,
high-traffic anchors. New material belongs in the narrowest owning directory.

## Coverage Map

The repository currently has useful material in every target category, but
three areas are overloaded:

| Current surface | Current problem | Migration treatment |
| --- | --- | --- |
| `docs/README.md` | Mixes onboarding, reference, product direction, research, and governance in one long list | Replace with a short audience-and-task router; preserve links through category indexes |
| `docs/*.md` | Concepts, contracts, integrations, operations, and roadmaps share one flat namespace | Keep only proven stable anchors; move lower-traffic files by owner and repair inbound links |
| `docs/product/*.md` | Runtime experiments, product foundations, surfaces, and use cases are interleaved | Group under `foundations/`, `runtimes/`, `surfaces/`, and `use-cases/` |
| `docs/reference/protocols/*.md` | Versioned contracts are flat and hard to scan | First group the index by domain; move files only with a separate protocol-path compatibility review |
| `benchmark/` | Current benchmark research needs a small RFC-linked home outside product packages | Keep protocols and public-safe practice here; place retired implementations and immutable experiment snapshots according to the canonical [archive placement rules](../../benchmark/README.md#archive-placement) |

This is a coverage-preserving migration. Unique claims, public evidence, and
useful links must either remain at their current path or appear in a new
canonical index. A shorter landing page is not permission to delete material.

## Stable Root Anchors

The first migration keeps these high-traffic paths stable:

- `docs/architecture.md`
- `docs/integration.md`
- `docs/state-interaction-model.md`
- `docs/status-data-contract.md`
- `docs/quota-allocation.md`
- `docs/heartbeat-automation-prompt.md`
- `docs/project-agent-todo-contract.md`
- `docs/public-private-boundary.md`

Their location can be reconsidered only when there is concrete evidence that
callers have migrated or a real compatibility mechanism exists. GitHub does
not provide transparent Markdown redirects, so creating a prettier tree is not
enough reason to break a widely linked path.

## Migration Rules

1. **Move by ownership, not by filename similarity.** A document belongs where
   its future changes will be reviewed, not where its title happens to fit.
2. **Preserve unique information.** Build a before/after coverage map for each
   index and keep every still-valid claim and public link reachable.
3. **Repair every repository caller.** Update Markdown links, examples, smokes,
   source comments, and generated navigation in the same change as a move.
4. **Treat external compatibility explicitly.** Keep a stable path when it is
   part of public onboarding, release notes, protocol documentation, or common
   contributor links. Do not leave dozens of placeholder files merely to make
   a directory look empty.
5. **Keep indexes selective.** A category index explains what belongs there
   and highlights canonical entry points. It is not required to repeat every
   historical filename.
6. **Separate current truth from evidence.** Stable guidance links to evidence;
   research packets do not become first-line operational instructions.
7. **Fail closed on private material.** Internal conversations, personal
   attribution, private URLs, local paths, credentials, raw transcripts, and
   permission diagnostics do not enter public docs.
8. **Validate the rendered path.** Check relative links, anchors, Mermaid, and
   the first visible section of changed landing pages before delivery.

## Staged Migration

### Stage 1: Navigation And Obvious Ownership

- add short indexes for architecture, concepts, operations, integrations, and
  product subdomains;
- publish public-safe RFCs below `docs/architecture/rfcs/`;
- move low-traffic root files whose owner is unambiguous;
- group product documents by foundations, runtime, surface, and use case;
- keep stable root anchors in place;
- add focused relative-link validation.

### Stage 2: Protocol Discovery

- group the protocol index by control plane, runtime integration, domain
  capability, evidence, and release/quality;
- inventory code and external references before moving any versioned protocol;
- keep protocol filenames and version suffixes stable.

### Stage 3: Research Lifecycle

- provide topic indexes for active findings, reusable evidence, dated packets,
  and archived decisions;
- keep source-backed artifacts inspectable;
- archive superseded packets without rewriting history.

## Visual Evidence Retention

Keep executable scenarios and synthetic fixture inputs in Git. Generated
screenshots, recordings and capture manifests describe one execution; store them
in ignored `output/playwright/` or `.local/` and attach the selected public-safe
evidence to the PR. The first-screen review requirement still applies, including
before/after views, tested revision, build/data boundary and material updates.
Attaching evidence does not require committing its binary files.

| Material | Retention |
| --- | --- |
| Test source and fixture inputs needed to reproduce a durable invariant | Track with the owning test; preserve independent and negative cases |
| Images used by a current guide, design reference or shipped product | Keep one canonical copy, referenced by its consumers |
| A fixed visual comparison fixture | Track only when its test or explicitly versioned comparison contract needs those exact bytes |
| One-run screenshots, recordings, capture JSON and before/after galleries | Ignored output plus PR attachments or a durable, revision-pinned artifact |
| Published release/showcase media | Keep its working distribution path until a verified replacement and all consuming links are ready |
| Superseded capture packs without an active consumer | Remove from the current tree; retain original evidence through its fixed Git revision or PR |

A README that only indexes a capture pack is not, by itself, a reason to keep
the pack. Before adding tracked media, name its actual reader or fixture
contract in the PR. Reuse existing assets; when a view is unchanged, say so
and reference one image instead of committing identical before/after copies.
Do not create another archive directory in the same repository for retired
binary evidence. The old `examples/personal-workspace-browser/evidence/` and
`screenshots/` output locations are ignored to prevent renewed accumulation.

Before removing a pack, inspect references across documentation, navigation,
source code, tests and distribution/build scripts, including references to its
README or manifest. Preserve transitive consumers and distinguish fixture reads
from screenshot output paths. No static references is a review signal, not proof
of disuse. Repair current links in the same change, preserve the bytes of retained
assets, and run the relevant link/asset checks. Retire tests only under the
[smoke retention rules](good-smokes.md), not because their files are large.

Historical capture packs from the initial cleanup remain inspectable at fixed
revision `bcb1a8710844bda6b84c2a00cf5d86ba78e126d9`:
[development captures](https://github.com/loopx-project/loopx/tree/bcb1a8710844bda6b84c2a00cf5d86ba78e126d9/docs/development/assets),
[browser captures](https://github.com/loopx-project/loopx/tree/bcb1a8710844bda6b84c2a00cf5d86ba78e126d9/examples/personal-workspace-browser),
and [workspace media](https://github.com/loopx-project/loopx/tree/bcb1a8710844bda6b84c2a00cf5d86ba78e126d9/docs/assets/personal-workspace).
Use that revision and the original PR to inspect a past result; newly generated
captures do not replace historical evidence. Deleting files from the current
tree does not shrink existing Git history. Rewriting shared history is a
separate maintainer decision, outside routine cleanup.

可执行场景和可复现的合成 fixture 输入保留在 Git；单次运行的截图、录像和 capture
manifest 放到已忽略的 `output/playwright/` 或 `.local/`，将选定的公开安全证据附在
PR 中。首屏审阅仍要求前后对照、实际版本、构建/数据边界和变更后的证据更新，
但不要求把这些二进制文件提交进仓库。

当前指南、设计规范或产品使用的图片保留一份，多个入口复用。固定视觉 fixture
只有在测试或明确的版本化比较合同需要其原始字节时才长期保留。发布和展示媒体
须先验证替代地址并迁好所有引用，再移除原分发文件。仅为截图包建立 README 不构成
长期保留理由；前后视图相同则明确说明并复用一张图。退役产物留在固定 Git 提交或
原 PR 中，不搬到仓库内另一个 archive 目录。

清理前核对文档、导航、代码、测试和构建/分发脚本的引用，包括 README/manifest 的
间接引用；区分 fixture 输入和截图输出路径。静态无引用只是审阅线索。当前引用须
保持可用，保留资源的字节不变，并执行对应链接/资源检查。测试按独立语义和覆盖面
判断保留，不按体积删除。上述固定版本可回看这次清理前的历史证据；重新生成的图
不能替代旧结果。删除当前文件不会清除 Git 历史，重写共享历史须另行决策。

## Placement Checklist

Before adding a public document, answer:

1. Who is expected to act after reading it?
2. Is it guidance, a concept, a product decision, a protocol, evidence, or a
   historical record?
3. Which existing index owns that reader and change reason?
4. Does the document contain only public-safe sources and examples?
5. What focused validation proves its links and presentation remain usable?

If no category owns the document, refine its purpose before creating another
top-level file.
