"""知识库文档解析与切块：把上传文件抽取为纯文本并切成检索块。

支持 txt / md / pdf / docx 四种格式；切块用 LangChain 的递归字符切分器，
中文分隔符优先，保证块边界尽量落在句子或段落上。
"""

from pathlib import Path

from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

# 扩展名 -> 归一化类型（kb_documents.file_type）
EXT_TYPES: dict[str, str] = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".pdf": "pdf",
    ".docx": "docx",
}

# 单文件上限：超过直接拒绝，避免解析拖垮服务
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

CHUNK_SIZE = 600
CHUNK_OVERLAP = 100

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
)


def detect_type(filename: str) -> str | None:
    """按扩展名识别文档类型；不支持的格式返回 None。"""
    return EXT_TYPES.get(Path(filename).suffix.lower())


def extract_text(path: Path, file_type: str) -> str:
    """从文件抽取纯文本。解析失败抛 ValueError（调用方转为入库失败状态）。"""
    try:
        if file_type in ("txt", "md"):
            return path.read_text(encoding="utf-8")
        if file_type == "pdf":
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        if file_type == "docx":
            doc = DocxDocument(str(path))
            parts = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    parts.append("　".join(cell.text for cell in row.cells))
            return "\n".join(part for part in parts if part)
    except (OSError, ValueError) as exc:
        raise ValueError(f"解析失败：{exc}") from exc
    raise ValueError(f"不支持的文档类型：{file_type}")


def split_chunks(text: str) -> list[str]:
    """切块并丢弃空白块；返回可直接向量化的文本列表。"""
    cleaned = text.strip()
    if not cleaned:
        return []
    return [chunk.strip() for chunk in _splitter.split_text(cleaned) if chunk.strip()]
