from __future__ import annotations

from app.agent.recall_booster import Document, boost_law_recall


def _doc(law_title: str, article: str, source: str = "law.docx") -> Document:
    return Document(
        page_content=f"{law_title}{article} 条文内容",
        metadata={
            "law_title": law_title,
            "article": article,
            "source": source,
        },
    )


def test_unsigned_contract_dismissal_boosts_correct_articles() -> None:
    mapping = {
        "中华人民共和国劳动合同法 第十条": [_doc("中华人民共和国劳动合同法", "第十条")],
        "中华人民共和国劳动合同法 第八十二条": [_doc("中华人民共和国劳动合同法", "第八十二条")],
        "中华人民共和国劳动合同法 第八十七条": [_doc("中华人民共和国劳动合同法", "第八十七条")],
        "中华人民共和国劳动合同法 第四十八条": [_doc("中华人民共和国劳动合同法", "第四十八条")],
        "中华人民共和国劳动合同法 第四十七条": [_doc("中华人民共和国劳动合同法", "第四十七条")],
    }

    def fake_search(query: str, top_k: int):
        return mapping.get(query, [])

    boosted = boost_law_recall(
        "没签合同被违法辞退",
        [],
        search_func=fake_search,
    )

    articles = {doc.metadata.get("article") for doc in boosted}
    assert {"第十条", "第八十二条", "第八十七条", "第四十八条", "第四十七条"} <= articles


def test_owed_wages_boosts_correct_articles() -> None:
    mapping = {
        "中华人民共和国劳动合同法 第三十条": [_doc("中华人民共和国劳动合同法", "第三十条")],
        "中华人民共和国劳动合同法 第八十五条": [_doc("中华人民共和国劳动合同法", "第八十五条")],
        "中华人民共和国劳动法 第五十条": [_doc("中华人民共和国劳动法", "第五十条")],
    }

    def fake_search(query: str, top_k: int):
        return mapping.get(query, [])

    boosted = boost_law_recall(
        "公司拖欠我3个月工资",
        [],
        search_func=fake_search,
    )

    articles = {doc.metadata.get("article") for doc in boosted}
    assert "第三十条" in articles
    assert "第八十五条" in articles


def test_no_match_returns_empty() -> None:
    calls: list[str] = []

    def fake_search(query: str, top_k: int):
        calls.append(query)
        return []

    boosted = boost_law_recall(
        "今天天气怎么样",
        [],
        search_func=fake_search,
    )

    assert boosted == []
    assert calls == []


def test_boosted_metadata_validation_filters_mismatch() -> None:
    def fake_search(query: str, top_k: int):
        return [_doc("中华人民共和国劳动合同法", "第八十一条")]

    boosted = boost_law_recall(
        "没签合同",
        ["未签劳动合同 员工权益"],
        search_func=fake_search,
    )

    assert boosted == []
