from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

try:
    from langchain_core.documents import Document
except Exception:  # pragma: no cover - fallback for import-stubbed tests
    @dataclass
    class Document:  # type: ignore[override]
        page_content: str
        metadata: dict[str, Any] = field(default_factory=dict)


LABOR_ELEMENT_TO_ARTICLES: dict[str, list[tuple[str, str]]] = {
    "未签劳动合同": [
        ("中华人民共和国劳动合同法", "第十条"),
        ("中华人民共和国劳动合同法", "第八十二条"),
    ],
    "违法解除": [
        ("中华人民共和国劳动合同法", "第八十七条"),
        ("中华人民共和国劳动合同法", "第四十八条"),
        ("中华人民共和国劳动合同法", "第四十七条"),
    ],
    "违法辞退": [
        ("中华人民共和国劳动合同法", "第八十七条"),
        ("中华人民共和国劳动合同法", "第四十八条"),
        ("中华人民共和国劳动合同法", "第四十七条"),
    ],
    "经济补偿": [
        ("中华人民共和国劳动合同法", "第四十六条"),
        ("中华人民共和国劳动合同法", "第四十七条"),
    ],
    "拖欠工资": [
        ("中华人民共和国劳动合同法", "第三十条"),
        ("中华人民共和国劳动合同法", "第八十五条"),
        ("中华人民共和国劳动法", "第五十条"),
    ],
    "试用期": [
        ("中华人民共和国劳动合同法", "第十九条"),
        ("中华人民共和国劳动合同法", "第八十三条"),
    ],
    "工伤": [
        ("工伤保险条例", "第三十七条"),
    ],
    "未缴社保": [
        ("中华人民共和国社会保险法", "第八十四条"),
        ("中华人民共和国劳动合同法", "第三十八条"),
    ],
}

_ELEMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "未签劳动合同": (
        "未签劳动合同",
        "未签合同",
        "没签劳动合同",
        "没签合同",
        "未签书面劳动合同",
        "未订立书面劳动合同",
    ),
    "违法解除": (
        "违法解除",
        "违法辞退",
        "违法开除",
        "无故辞退",
        "口头辞退",
        "突然辞退",
    ),
    "违法辞退": (
        "违法辞退",
        "违法解除",
        "违法开除",
        "无故辞退",
        "口头辞退",
        "突然辞退",
    ),
    "经济补偿": (
        "经济补偿",
        "补偿金",
        "赔偿金",
        "2N",
        "代通知金",
    ),
    "拖欠工资": (
        "拖欠工资",
        "欠薪",
        "未发工资",
        "克扣工资",
        "无故拖欠工资",
    ),
    "试用期": (
        "试用期",
        "违法约定试用期",
    ),
    "工伤": (
        "工伤",
        "伤残",
    ),
    "未缴社保": (
        "未缴社保",
        "没缴社保",
        "未缴纳社保",
        "未交社保",
        "社保未缴",
    ),
}

SearchFunc = Callable[[str, int], Iterable[Any]]


def boost_law_recall(
    question: str,
    expansion_queries: list[str],
    *,
    search_func: SearchFunc | None = None,
) -> list[Document]:
    """
    Deterministically inject law/article exact-recall candidates based on
    high-signal labor-law elements found in the question or its expansions.
    """
    if search_func is None:
        return []

    targets = _collect_boost_targets(question, expansion_queries)
    if not targets:
        return []

    boosted: list[Document] = []
    seen: set[tuple[str, str, str]] = set()

    for law_title, article in targets:
        exact_query = f"{law_title} {article}"
        raw_hits = list(search_func(exact_query, 1) or [])
        documents = _coerce_documents(raw_hits)
        if not documents:
            continue

        validated = _validate_boost_hit(documents[0], law_title, article, exact_query)
        if validated is None:
            continue

        key = _document_key(validated)
        if key in seen:
            continue
        seen.add(key)
        boosted.append(validated)

    return boosted


def _collect_boost_targets(question: str, expansion_queries: list[str]) -> list[tuple[str, str]]:
    merged_text = "\n".join(
        part.strip()
        for part in [question, *expansion_queries]
        if isinstance(part, str) and part.strip()
    )
    if not merged_text:
        return []

    targets: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for element, article_refs in LABOR_ELEMENT_TO_ARTICLES.items():
        if not _matches_element(merged_text, element):
            continue
        for item in article_refs:
            if item in seen:
                continue
            seen.add(item)
            targets.append(item)
    return targets


def _matches_element(text: str, element: str) -> bool:
    aliases = _ELEMENT_ALIASES.get(element, (element,))
    if any(alias in text for alias in aliases):
        return True

    if element == "拖欠工资":
        return "拖欠" in text and "工资" in text
    if element == "未签劳动合同":
        return "签" in text and "合同" in text and any(token in text for token in ("未", "没"))
    if element == "未缴社保":
        return "社保" in text and any(token in text for token in ("未缴", "未交", "没缴"))

    return False


def _coerce_documents(items: Iterable[Any]) -> list[Document]:
    documents: list[Document] = []
    for item in items:
        candidate = item[0] if isinstance(item, tuple) and item else item
        page_content = getattr(candidate, "page_content", None)
        metadata = getattr(candidate, "metadata", None)
        if not isinstance(page_content, str):
            continue
        safe_metadata = metadata if isinstance(metadata, dict) else {}
        documents.append(Document(page_content=page_content, metadata=dict(safe_metadata)))
    return documents


def _validate_boost_hit(
    document: Document,
    law_title: str,
    article: str,
    exact_query: str,
) -> Document | None:
    metadata = dict(document.metadata or {})
    actual_law_title = str(metadata.get("law_title") or "").strip()
    actual_article = str(metadata.get("article") or "").strip()

    if not _law_titles_match(actual_law_title, law_title):
        return None
    if actual_article != article:
        return None

    enriched_metadata = dict(metadata)
    enriched_metadata["_booster_query"] = exact_query
    enriched_metadata["_booster_target_law"] = law_title
    enriched_metadata["_booster_target_article"] = article
    return Document(page_content=document.page_content, metadata=enriched_metadata)


def _law_titles_match(actual: str, expected: str) -> bool:
    actual_core = _normalize_law_title(actual)
    expected_core = _normalize_law_title(expected)
    if not actual_core or not expected_core:
        return False
    return expected_core in actual_core or actual_core in expected_core


def _normalize_law_title(value: str) -> str:
    return (
        (value or "")
        .replace("中华人民共和国", "")
        .replace("《", "")
        .replace("》", "")
        .replace(" ", "")
        .strip()
    )


def _document_key(document: Document) -> tuple[str, str, str]:
    metadata = dict(document.metadata or {})
    return (
        str(metadata.get("law_title") or "").strip(),
        str(metadata.get("article") or "").strip(),
        str(metadata.get("source") or "").strip(),
    )
