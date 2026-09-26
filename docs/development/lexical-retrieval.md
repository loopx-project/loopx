# Shared lexical retrieval / 共享词法检索

`loopx.lexical_retrieval.score_bm25(documents, query)` is a standard-library-only
scorer, reused by the self-repair lookup and the financial-evidence consumer.
It accepts document strings and returns one score plus matched terms per input,
and corpus-wide unmatched query terms. It keeps input positions; the consumer
owns filtering, stable keys, tie-breaks, exact-match precedence and pagination.

`loopx.lexical_retrieval.score_bm25(documents, query)` 仅依赖标准库，自修复查询和
金融证据消费者共用它。输入文档字符串，返回与每个输入对应的分数、匹配词项，以及整个
语料未命中的查询词项。保留输入位置；筛选、稳定身份、同分排序、准确匹配优先级和分页
由消费者负责。

```python
from loopx.lexical_retrieval import score_bm25

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
