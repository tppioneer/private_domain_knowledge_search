"""轻量全文检索 —— 基于 SQLite FTS5（BM25 排序）。"""

from __future__ import annotations

import logging
import sqlite3
import os

from ....models.schemas import KnowledgeItem, KnowledgeMeta, KnowledgeType, SearchContext
from . import sanitize_fts5_query

logger = logging.getLogger(__name__)


class LiteBM25Searcher:
    """SQLite FTS5 全文检索，支持 BM25 排序。无需外部服务。"""

    def __init__(self, db_path: str = "./data/knowledge.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                doc_id, type, content, title, module,
                meta_json,
                tokenize='unicode61'
            )
        """)
        conn.commit()
        conn.close()

    @property
    def available(self) -> bool:
        return True

    def index_documents(self, docs: list[dict]) -> int:
        """批量索引文档。doc = {id, type, content, title?, module?, meta_json?}。"""
        conn = sqlite3.connect(self.db_path)
        count = 0
        for doc in docs:
            conn.execute(
                "INSERT INTO knowledge_fts(doc_id, type, content, title, module, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    doc.get("id", ""),
                    doc.get("type", "api"),
                    doc.get("content", ""),
                    doc.get("title", ""),
                    doc.get("module", ""),
                    doc.get("meta_json", "{}"),
                ),
            )
            count += 1
        conn.commit()
        conn.close()
        return count

    async def search(
        self,
        query: str,
        context: SearchContext | None = None,
        knowledge_types: list[KnowledgeType] | None = None,
        top_k: int = 15,
    ) -> list[KnowledgeItem]:
        conn = sqlite3.connect(self.db_path)

        # FTS5 多词默认 AND，改为 OR 提升召回；特殊字符消毒
        fts_query = sanitize_fts5_query(query)
        conditions = ["knowledge_fts MATCH ?"]
        params: list = [fts_query]

        if knowledge_types:
            placeholders = ",".join(["?"] * len(knowledge_types))
            conditions.append(f"type IN ({placeholders})")
            params.extend([t.value for t in knowledge_types])

        if context and context.module:
            conditions.append("module = ?")
            params.append(context.module)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT doc_id, type, content, meta_json, bm25(knowledge_fts, 0,0,0,0) AS score
            FROM knowledge_fts
            WHERE {where}
            ORDER BY score
            LIMIT ?
        """
        params.append(top_k)

        try:
            rows = conn.execute(sql, params).fetchall()
            items: list[KnowledgeItem] = []
            raw_scores: list[float] = []
            for row in rows:
                doc_id, item_type, content, meta_json, raw_score = row
                meta = {}
                if meta_json:
                    import json
                    try:
                        meta = json.loads(meta_json)
                    except Exception:
                        pass
                try:
                    kt = KnowledgeType(item_type)
                except ValueError:
                    kt = KnowledgeType.API
                items.append(KnowledgeItem(
                    id=doc_id,
                    type=kt,
                    content=content,
                    score=0.0,  # 占位，下面统一归一化
                    meta=KnowledgeMeta(**meta),
                ))
                raw_scores.append(raw_score)

            # 结果集内相对归一化：最高分→1.0，最低分→0.1，线性映射
            if raw_scores:
                max_s = max(raw_scores)
                min_s = min(raw_scores)
                span = max_s - min_s if max_s != min_s else 1.0
                for i, item in enumerate(items):
                    item.score = round(0.1 + 0.9 * (raw_scores[i] - min_s) / span, 4)

            return items
        except Exception:
            logger.exception("bm25 search failed: query=%s", query[:100])
            return []
        finally:
            conn.close()
