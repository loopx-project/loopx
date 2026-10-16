# Research history Lark-whiteboard board

Renders one Goal's recorded Explore history as an SVG for a Lark whiteboard.

The board answers the long-run reading questions from the two-layer workspace
contract (`docs/architecture/rfcs/goal-workspace-surface-v0.zh-CN.md` §5.11):
what the early/middle/late thirds of record time contain, where recording gaps
are, which recorded routes explicit edges/parent grouping connect, and — when
findings use the Explore writeback sections — what changed, stopped and was
retained at each explicit replan/continue.

It is a **display projection only**: thirds are reading groups, not a
pre-declared evaluation window; marker position is record time, not attempt
time or progress; unlinked questions never acquire edges; findings labels stay
source labels; no native score or evaluation is rendered.

This static example is a bounded excerpt, not the interactive workspace: it
discloses the displayed/total route and decision counts, shows at most eight
linked groups and six recent decisions, and keeps repeated decisions for the
same question. Decision ordering and phase selection reuse the workspace's
shared presentation owner. Missing record times stay unknown.

## Inputs

Either an append-only `explore-result-log.jsonl`, or one of the captured-event
wrappers in the [three-checkpoint reproduction guide](../personal-workspace-browser/research-history.md).
The Portfolio example is
`apps/presentation/dashboard/src/data/fixtures/edgebench-research-history.json`.

```sh
node --no-warnings --experimental-strip-types examples/research-history-lark/board.mts \
  <runtime>/goals/<goal>/explore-result-log.jsonl /tmp/research.svg "<Title>" <goal_id>
```

## Whiteboard publish

Follow the `lark-whiteboard` skill (render/geometric check → OpenAPI → write):

```sh
npx -y @larksuite/whiteboard-cli@^0.2.13 -i /tmp/research.svg -f svg --check
npx -y @larksuite/whiteboard-cli@^0.2.13 -i /tmp/research.svg -o /tmp/research.png -f svg
npx -y @larksuite/whiteboard-cli@^0.2.13 -i /tmp/research.svg -f svg --to openapi --format json \
  | lark-cli whiteboard +update --as user --whiteboard-token <token> \
      --source - --input_format raw \
      --idempotent-token <unique-token>
```

Publish only data authorized for that destination. The example's external
trajectory publication does not authorize publishing an unrelated live Goal.
