# Shared lexical retrieval / 共享词法检索

`loopx.capabilities.context_providers.lexical_retrieval.score_bm25(documents, query)` is a standard-library-only
scorer, reused by the self-repair lookup and the financial-evidence consumer.
It accepts document strings and returns one score plus matched terms per input,
and corpus-wide unmatched query terms. It keeps input positions; the consumer
owns filtering, stable keys, tie-breaks, exact-match precedence and pagination.
Repository review experience and the local ContextProvider also reuse this
same scorer; they keep their own source and qualification boundaries.

`loopx.capabilities.context_providers.lexical_retrieval.score_bm25(documents, query)` 仅依赖标准库，自修复查询和
金融证据消费者共用它。输入文档字符串，返回与每个输入对应的分数、匹配词项，以及整个
语料未命中的查询词项。保留输入位置；筛选、稳定身份、同分排序、准确匹配优先级和分页
由消费者负责。

```python
from loopx.capabilities.context_providers.lexical_retrieval import score_bm25

result = score_bm25(["lease recovery proof", "display refresh"], "recovery proof")
assert result.documents[0].score > result.documents[1].score
```

The fixed algorithm is BM25 with k1=1.2, b=.75 and positive Lucene IDF.
Casefolded Unicode word tokenization splits underscores. Repeated query terms
do not boost scores. No synonyms, embeddings, segmentation service, discovery,
storage, configuration, network or Goal authority are introduced. Ranking is
corpus-relative; a score does not certify a fact, risk, investment edge or trade.
Historical consumers must supply only their visible corpus: future documents
would otherwise change document frequencies even if hidden from the results.

固定算法为 BM25，k1=1.2、b=.75、Lucene 正 IDF。Unicode 词项经 casefold，
下划线分隔；重复查询词项不增加权重。不引入同义词、嵌入、分词服务、发现、存储、配置、
网络或 Goal 权威。分数相对于语料，不认证事实、风险、投资优势或交易资格。历史查询的
消费者必须只传入可见语料，否则未来文档即使不展示，也会改变文档频率。

`score_bm25(..., tokenizer=...)` optionally selects analysis for both the query
and every document. Omission is exactly the existing `lexical_tokens` default.
This is one shared scorer, not a separately copied BM25 implementation. The
repair/review/evidence consumers keep their previous behavior. Specialized
computation and file/provider transport remain Python; no parallel state or
decision owner is introduced by this bounded extension.

The implementation lives with the existing context-provider owner, not at the
package root. Repair lookup, repository review, and the BM25 adapter import the
same module; installation/wheel packaging copies that exact standalone source.
`loopx.lexical_retrieval` contains only compatibility re-exports for independently
distributed evidence consumers of the previously documented import. It owns no
algorithm, registry, state, or new caller; remove that facade only after those
consumers migrate. No new generic utility package or capability is introduced.

## Local context provider

`loopx.capabilities.context_providers.bm25` adapts one explicit local corpus to
the existing `ContextProvider` port. `provider=bm25` is default-off through an
owner-local Decision Context profile. It makes no model calls or network
requests, writes no index or memory, and adds no host hook. The same profile and
native recall command can instead select OpenViking or an activated extension.
The PR-review experience reader retains its domain qualification/envelopes and
does not become an arbitrary local-file reader.

Create an ignored private corpus:

```json
{
  "schema_version": "local_bm25_corpus_v0",
  "scope_ref": "corpus:engineering-notes",
  "documents": [
    {"id": "output-budget", "content": "Measure stdout UTF-8 bytes and preserve decision obligations."},
    {"id": "effect-recovery", "content": "Read the existing effect before retrying an uncertain write."}
  ]
}
```

The format is local to this provider, with at most 8 MiB, 1,000 documents and
16,000 characters per document. Duplicate ids, unknown fields, invalid UTF-8
and unstable/non-regular file reads are rejected through the existing bounded
material reader. A request scope must equal the corpus scope; it never becomes
a path to read. All results are untrusted advisory input, with no permission,
Goal, Todo, claim, lease, publication or execution authority.

Use this provider-only private profile:

```json
{
  "schema_version": "decision_context_profile_v0",
  "goal_id": "<goal-id>",
  "enabled": true,
  "enabled_agents": ["<agent-id>"],
  "source_provider_bindings": [],
  "sources": [],
  "context_provider": {
    "provider": "bm25",
    "namespace": "engineering-notes",
    "max_results": 3,
    "timeout_seconds": 10,
    "config": {"corpus_path": "<absolute-private-corpus.json>", "han_bigrams": false}
  },
  "automation": {"automatic_capture": false, "fail_open": true}
}
```

```bash
loopx decision-context recall-context \
  --goal-id <goal-id> --agent-id <agent-id> --profile <private-profile.json> \
  --context-scope-ref 'corpus:engineering-notes' \
  --query 'stdout UTF-8 bytes' --query-summary 'Output budget evidence' \
  --max-results 3 --format json
```

The outer packet is local-private transient. Its nested public receipt uses a
generic summary and opaque document reference, omitting content and source path.
Inspect status/reason: a missing/corrupt corpus is unavailable; a completed
zero-match search is empty. `sync` is explicitly read-only. Disable by setting
this profile's `enabled=false` or removing its provider binding. No corpus is
deleted and no other Agent configuration is replaced.

`han_bigrams=true` is a separate opt-in treatment: NFKC normalization, the
shared casefolded word analyzer for non-Han runs, and overlapping bigrams for a
Han run (one token for a single character). The standard Unicode character
database identifies Han; no segmentation service, synonyms, model or second
ranker is added. This can help Chinese substring queries but must not be
reported as the historical word-default baseline. Keep both arms when testing
it. Scores use the same fixed BM25 parameters and remain corpus-relative.

## Native provider comparison

`examples/context-provider-comparison.py` calls the real read-only
`decision-context recall-context` command for each explicit profile/scope. A
new provider needs a qualified profile, not a driver-specific SDK adapter.
Freeze document bytes/granularity, queries, relevance labels and result limits.
For example, a private suite has this shape:

```json
{
  "schema_version": "context_provider_comparison_suite_v0",
  "corpus": "corpus.json",
  "max_results": 3,
  "queries": [
    {"id": "q1", "query": "stdout UTF-8 bytes", "relevant_ids": ["output-budget"]},
    {"id": "q2", "query": "commercial compensation policy", "relevant_ids": [], "unanswerable": true}
  ],
  "providers": [
    {"id": "bm25-words", "profile": "bm25-profile.json", "scope_ref": "corpus:engineering-notes"},
    {"id": "other", "profile": "other-profile.json", "scope_ref": "<exact-provider-scope>"}
  ]
}
```

```bash
uv run --extra test python examples/context-provider-comparison.py \
  --suite <private-suite.json> --goal-id <goal-id> --agent-id <agent-id> \
  --loopx-command-json '["loopx"]' > <private-report.json>
```

Read the other provider's stored L2 back against the same source bytes first.
Returned-content hashes determine relevance, normalizing only CRLF and one
terminal newline. Unmatched parser output remains visible; duplicate hits keep
their actual rank and cannot inflate recall. The driver reports hit rate,
recall/MRR at the cap, failures, returned characters, provider latency, full CLI
wall time and unknown-question candidate counts. Missing/unavailable queries
stay in denominators; incomplete or changed-input runs are marked incomplete
and exit nonzero. Corpus/suite/profile hashes are retained; raw queries/content
are not written into the local report. Private ids still need review before
publication.

Do not compare raw scores across providers or treat relevance as evidence that
an answer exists. Include unknown-answer cases. Run order and caches are not
controlled by this small driver, so timing samples are descriptive. It generates
no answer and measures no causal task utility. The suite/report formats are
local experiments, not new Core contracts.

Manual recall does not enable automatic memory. Current explicit instructions
belong to Semantic Preference; qualified procedural advice uses existing
`agent-turn-recall` after admission, and verified-outcome writeback remains
separate. Material recall still requires source rebase. No frontend comparison
editor or new Lark route is added or qualified in this provider/CLI slice.

The repair skill retains its parser, exact pattern/code precedence and complete
guidance expansion. The existing workflow installer and wheel bundle the
canonical scorer beside the script, so isolated execution still works without
LoopX on PATH. The packaged copy is a build/install artifact, not another source
implementation. Finance retains canonical instruments, literal match rules,
source/clock/lifecycle boundaries and decision authority in its own consumer.
No new automatic capability hook or UI/Lark configuration is enabled.

自修复 skill 保留解析器、准确 pattern/code 优先级和完整正文展开。现有 workflow
installer 和 wheel 将规范评分器打包到脚本旁，隔离运行仍无需 PATH 中的 LoopX。
打包副本是构建/安装产物，不是第二份实现。金融消费者保留规范资产、字面匹配规则、
信源/时点/lifecycle 边界和决策权威。不启用新的自动 capability hook 或 UI/Lark 配置。
