"""知识库接口：库的增删改查、文档上传与后台入库。

M1 范围：文档解析 -> 切块 -> 向量化落库；检索注入在 chat 侧接入（M2）。
知识库按用户隔离，管理员可访问任意库（与会话的归属校验语义一致）。
"""

import re
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    UploadFile,
)

from .. import auth, db
from ..db import User
from ..documents import MAX_UPLOAD_BYTES, detect_type, extract_text, split_chunks
from ..llm import embed_documents
from ..schemas.kb import (
    DeletedKnowledgeBase,
    DocumentOut,
    KBCreateRequest,
    KBUpdateRequest,
    KnowledgeBaseOut,
)

router = APIRouter(prefix="/api/kb", tags=["知识库"])

# 原始文件保存目录：backend/data/kb/<kb_id>/，便于日后重新入库
_KB_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "kb"

# DashScope 兼容的 embeddings 端点单次请求的文本条数上限
_EMBED_BATCH = 10

_FILENAME_SAFE = re.compile(r"[^\w.\u4e00-\u9fff-]+")


def _require_kb(kb_id: int, user: User):
    """取库并校验归属：不存在 404，他人库 403（管理员放行）。"""
    kb = db.get_knowledge_base(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if kb.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="无权访问该知识库")
    return kb


def _safe_name(filename: str) -> str:
    """只保留 basename 中的安全字符，防止路径穿越。"""
    name = Path(filename).name
    cleaned = _FILENAME_SAFE.sub("_", name).strip("._")
    return cleaned or "document"


def _ingest_document(doc_id: int, path: Path, file_type: str) -> None:
    """后台入库：解析 -> 切块 -> 分批向量化 -> 落块，全程更新文档状态。"""
    db.set_document_status(doc_id, "embedding")
    try:
        text = extract_text(path, file_type)
        chunks = split_chunks(text)
        if not chunks:
            raise ValueError("未能从文档中提取到可用文本")
        doc = db.get_document(doc_id)
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), _EMBED_BATCH):
            vectors.extend(embed_documents(chunks[i : i + _EMBED_BATCH]))
        db.insert_chunks(doc_id, doc["kb_id"], chunks, vectors)
        db.set_document_status(doc_id, "ready", chunk_count=len(chunks))
    except Exception as exc:  # noqa: BLE001 后台任务边界：任何异常都要落到 failed 状态
        db.set_document_status(doc_id, "failed", error=str(exc)[:2000])
    finally:
        path.unlink(missing_ok=True)


@router.post("", response_model=KnowledgeBaseOut)
def create_kb(
    req: KBCreateRequest, user: User = Depends(auth.get_current_user)
) -> KnowledgeBaseOut:
    """新建知识库（名称按当前用户唯一性不做限制，列表按创建时间倒序）。"""
    kb_id = db.create_knowledge_base(user.id, req.name.strip(), req.description)
    kb = db.get_knowledge_base(kb_id)
    return KnowledgeBaseOut(
        id=kb.id, name=kb.name, description=kb.description,
        doc_count=0, created_at=kb.created_at,
    )


@router.get("", response_model=list[KnowledgeBaseOut])
def list_kbs(
    keyword: str = Query(default="", description="按名称搜索"),
    user: User = Depends(auth.get_current_user),
) -> list[KnowledgeBaseOut]:
    """当前用户的知识库列表。"""
    return [KnowledgeBaseOut(**item) for item in db.list_knowledge_bases(user.id, keyword)]


@router.patch("/{kb_id}", response_model=KnowledgeBaseOut)
def update_kb(
    kb_id: int, req: KBUpdateRequest, user: User = Depends(auth.get_current_user)
) -> KnowledgeBaseOut:
    """修改知识库名称与描述：仅限本人（管理员可改任意库）。"""
    kb = _require_kb(kb_id, user)
    db.rename_knowledge_base(kb_id, req.name.strip(), req.description)
    kb = db.get_knowledge_base(kb_id)
    docs = db.list_kb_documents(kb_id)
    return KnowledgeBaseOut(
        id=kb.id, name=kb.name, description=kb.description,
        doc_count=len(docs), created_at=kb.created_at,
    )


@router.delete("/{kb_id}", response_model=DeletedKnowledgeBase)
def delete_kb(
    kb_id: int, user: User = Depends(auth.get_current_user)
) -> DeletedKnowledgeBase:
    """软删除知识库：文档与向量块保留在库内，列表与检索不再出现。"""
    _require_kb(kb_id, user)
    if not db.delete_knowledge_base(kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return DeletedKnowledgeBase(id=kb_id, deleted=True)


@router.post("/{kb_id}/documents", response_model=DocumentOut, status_code=201)
def upload_document(
    kb_id: int,
    file: UploadFile,
    background: BackgroundTasks,
    user: User = Depends(auth.get_current_user),
) -> DocumentOut:
    """上传文档并排队入库：接口立即返回 pending，前端轮询文档列表看进度。

    支持 txt / md / pdf / docx，单文件不超过 10 MB。
    """
    _require_kb(kb_id, user)
    filename = file.filename or "document"
    file_type = detect_type(filename)
    if file_type is None:
        raise HTTPException(status_code=415, detail="仅支持 txt / md / pdf / docx 文件")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=422, detail="文件内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 10 MB 上限")

    doc_id = db.add_document(kb_id, _safe_name(filename), file_type, len(data))
    doc_dir = _KB_DATA_DIR / str(kb_id)
    doc_dir.mkdir(parents=True, exist_ok=True)
    path = doc_dir / f"{doc_id}_{_safe_name(filename)}"
    path.write_bytes(data)

    background.add_task(_ingest_document, doc_id, path, file_type)
    return DocumentOut(**db.get_document(doc_id))


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(
    kb_id: int, user: User = Depends(auth.get_current_user)
) -> list[DocumentOut]:
    """知识库的文档列表（含入库状态，供前端轮询进度）。"""
    _require_kb(kb_id, user)
    return [DocumentOut(**item) for item in db.list_kb_documents(kb_id)]


@router.delete("/{kb_id}/documents/{doc_id}", response_model=dict)
def delete_document(
    kb_id: int, doc_id: int, user: User = Depends(auth.get_current_user)
) -> dict:
    """删除文档：向量块级联删除，原始文件一并清理。"""
    _require_kb(kb_id, user)
    doc = db.get_document(doc_id)
    if doc is None or doc["kb_id"] != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    db.delete_document(doc_id)
    for path in (_KB_DATA_DIR / str(kb_id)).glob(f"{doc_id}_*"):
        path.unlink(missing_ok=True)
    return {"id": doc_id, "deleted": True}
