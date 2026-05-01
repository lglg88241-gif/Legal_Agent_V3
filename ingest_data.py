import logging
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def build_source_documents() -> list[Document]:
    records = [
        {
            "id": "labor-law-001",
            "title": "违法解除劳动合同的赔偿责任",
            "article": "《中华人民共和国劳动合同法》第八十七条",
            "content": (
                "用人单位违反法律规定解除或者终止劳动合同的，应当依照经济补偿标准的二倍向劳动者支付赔偿金。"
                "劳动者月工资高于当地上年度职工月平均工资三倍的，支付赔偿金的月工资标准按职工月平均工资三倍计算，"
                "支付年限最高不超过十二年。该规则常用于无故辞退、违法裁员、以不成立理由解除劳动关系等情形。"
            ),
        },
        {
            "id": "labor-law-002",
            "title": "延长工作时间的加班费计算",
            "article": "《中华人民共和国劳动法》第四十四条",
            "content": (
                "安排劳动者延长工作时间的，支付不低于工资百分之一百五十的工资报酬；"
                "休息日安排劳动者工作又不能安排补休的，支付不低于工资百分之二百的工资报酬；"
                "法定休假日安排劳动者工作的，支付不低于工资百分之三百的工资报酬。"
                "实践中计算加班费时，通常需要结合劳动合同约定工资、考勤记录以及单位的排班制度综合认定。"
            ),
        },
        {
            "id": "labor-law-003",
            "title": "试用期期限与次数限制",
            "article": "《中华人民共和国劳动合同法》第十九条",
            "content": (
                "劳动合同期限三个月以上不满一年的，试用期不得超过一个月；"
                "劳动合同期限一年以上不满三年的，试用期不得超过二个月；"
                "三年以上固定期限和无固定期限的劳动合同，试用期不得超过六个月。"
                "同一用人单位与同一劳动者只能约定一次试用期，且以完成一定工作任务为期限的劳动合同或者"
                "劳动合同期限不满三个月的，不得约定试用期。"
            ),
        },
        {
            "id": "labor-law-004",
            "title": "未订立书面劳动合同的双倍工资",
            "article": "《中华人民共和国劳动合同法》第八十二条",
            "content": (
                "用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，"
                "应当向劳动者每月支付二倍工资。该责任通常从用工满一个月的次日起开始计算，"
                "截至补订书面劳动合同之日或者用工满一年之日止。对于劳动者主张双倍工资差额，"
                "证据重点一般包括入职时间、工资流水、工作记录和双方沟通材料。"
            ),
        },
        {
            "id": "labor-law-005",
            "title": "竞业限制与经济补偿",
            "article": "《中华人民共和国劳动合同法》第二十三条、第二十四条",
            "content": (
                "对负有保密义务的劳动者，用人单位可以在劳动合同或者保密协议中约定竞业限制条款，"
                "并在解除或者终止劳动合同后，在竞业限制期限内按月给予劳动者经济补偿。"
                "竞业限制期限不得超过二年。若单位长期不支付竞业限制补偿，劳动者可以依法主张解除竞业限制约定，"
                "并结合约定违约金、岗位性质和保密范围判断责任承担。"
            ),
        },
    ]

    return [
        Document(
            page_content=f"{item['title']}\n{item['article']}\n{item['content']}",
            metadata={
                "doc_id": item["id"],
                "title": item["title"],
                "article": item["article"],
                "domain": "labor_law",
                "source": "synthetic_seed_data",
            },
        )
        for item in records
    ]


def resolve_embedding_model_path(model_name: str) -> str:
    configured_path = Path(model_name)
    if configured_path.exists():
        return str(configured_path)

    hub_root = Path.home() / ".cache" / "huggingface" / "hub"
    repo_dir = hub_root / f"models--{model_name.replace('/', '--')}"
    ref_file = repo_dir / "refs" / "main"
    if ref_file.exists():
        snapshot_id = ref_file.read_text(encoding="utf-8").strip()
        snapshot_dir = repo_dir / "snapshots" / snapshot_id
        if snapshot_dir.exists():
            return str(snapshot_dir)

    return model_name


def main() -> None:
    persist_dir = Path(settings.CHROMA_PERSIST_DIR)
    persist_dir.mkdir(parents=True, exist_ok=True)

    embedding_model_path = resolve_embedding_model_path(settings.EMBEDDING_MODEL_PATH)
    logger.info("Loading embedding model: %s", embedding_model_path)
    embeddings = HuggingFaceEmbeddings(
        model_name=embedding_model_path,
        model_kwargs={"device": "cpu", "local_files_only": True},
        encode_kwargs={"normalize_embeddings": True},
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=180,
        chunk_overlap=30,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )

    source_docs = build_source_documents()
    chunks = splitter.split_documents(source_docs)
    ids = [f"{chunk.metadata['doc_id']}-chunk-{index}" for index, chunk in enumerate(chunks, start=1)]

    vectorstore = Chroma(
        collection_name="labor_law_collection",
        embedding_function=embeddings,
        persist_directory=str(persist_dir),
    )

    # Keep reruns idempotent for the seeded dataset.
    vectorstore.delete(ids=ids)
    vectorstore.add_documents(documents=chunks, ids=ids)

    logger.info(
        "Seeded %s source documents into %s chunks at %s",
        len(source_docs),
        len(chunks),
        persist_dir,
    )


if __name__ == "__main__":
    main()
