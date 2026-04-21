# Article Reference Graph

## Design Motivation

在 D4 权益清单场景的诊断中，单纯依赖 query expansion + hybrid recall + rerank，容易把高频但不相关的程序性条文、泛义劳动法条文排到前面，而把真正有交叉引用关系的核心条文压下去。

为此，增加了一个独立的法条交叉引用图 ingest 能力：

- 从现有法律条文向量库中扫描所有 `law_title + article` 元数据完整的 chunk
- 从条文正文中抽取 “第X条” 形式的内部交叉引用
- 写入 Neo4j，形成 `(LawArticle)-[:REFERENCES]->(LawArticle)` 图

该图数据本身是健康的，后续仍可用于 query-aware rerank、seed refinement 或 explainability。

## Schema

### Node

Label: `LawArticle`

Properties:

- `law_title`: 法律全称
- `article_num`: 阿拉伯数字条号
- `article_label`: 原始条号文本，例如 `第八十七条`

唯一约束：

```cypher
CREATE CONSTRAINT law_article_unique IF NOT EXISTS
FOR (n:LawArticle)
REQUIRE (n.law_title, n.article_num) IS UNIQUE
```

### Edge

Relationship: `(:LawArticle)-[:REFERENCES]->(:LawArticle)`

含义：

- 单向表示源条文正文中显式引用了目标条文
- 运行时若需要扩展召回，应做双向遍历

## Ingest Script Usage

脚本位置：

- [scripts/build_article_reference_graph.py](/C:/Users/123/Legal_Agent_V3/scripts/build_article_reference_graph.py)

执行方式：

```powershell
.\.venv\Scripts\python.exe scripts\build_article_reference_graph.py
```

脚本行为：

1. 连接现有 Chroma 法律向量库
2. 扫描所有 `metadata.article` 与 `metadata.law_title` 完整的 law chunk
3. 用正则抽取正文中的 `第X条`
4. 中文数字转阿拉伯数字
5. 启动时先清空旧的 `LawArticle` 节点和 `REFERENCES` 边
6. 写入 Neo4j，并在结束时打印统计结果

## Current Stats

基于当前法律库一次性 ingest 的结果：

- `LawArticle` 节点数：2124
- `REFERENCES` 边数：223

入度 top 5：

1. 《中华人民共和国民法典》第五百一十条，26
2. 《中华人民共和国刑法》第二百三十二条，6
3. 《中华人民共和国刑法》第二百三十四条之一，6
4. 《中华人民共和国劳动合同法》第四十条，5
5. 《中华人民共和国刑法》第六十九条，4

《中华人民共和国劳动合同法》第八十七条的邻居示例：

- 第四十七条
- 第四十八条

这说明图数据能正确反映“违法解除赔偿金”条文与“经济补偿计算”“继续履行/赔偿”条文之间的显式引用关系。

## Known Limitations

- 边密度较低。大多数法条正文不包含显式交叉引用，因此图天然是稀疏图。
- 目前只覆盖类似“本法第X条”“第X条”的内部引用格式，未处理更复杂的文本指代。
- 跨法律引用尚未处理。例如判例或条文中引用其他法律时，当前 Lite 版不会建立跨法律边。
- C2 曾尝试把该图直接接入运行时 law retrieval，但该集成已经回退。
  根因在 rerank 阶段的偏置，而不是图扩展本身的数据质量问题。
  后续若要重新集成，应优先做 query-aware rerank 或更严格的 seed 过滤，而不是继续直接把邻居候选混入当前 top_k 流程。
