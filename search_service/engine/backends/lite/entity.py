"""轻量实体查询 —— 基于 SQLite 的精确实体匹配。

支持多种匹配策略，按优先级降序：
1. 全限定类名精确匹配：com.xxx.file.FileServiceManager
2. 简单类名精确匹配：FileServiceManager
3. 类名包含匹配：LLM → com.xxx.llm.api.LLM
4. 方法名匹配：generate → 匹配到 LLM.generate
"""

from __future__ import annotations

import json
import logging
import sqlite3
import os

from . import sanitize_fts5_query

logger = logging.getLogger(__name__)


class LiteEntitySearcher:
    """基于 SQLite + 内存过滤的实体精确查询。"""

    def __init__(self, db_path: str = "./data/knowledge.db"):
        self.db_path = db_path
        self._available = os.path.exists(db_path)

    @property
    def available(self) -> bool:
        return self._available

    def search_entity(
        self,
        name: str,
        entity_type: str | None = None,
    ) -> list[dict]:
        """搜索匹配的实体，返回按匹配质量排序的候选列表。

        每个候选: {chunk: dict, match_type: str, score: float}
          match_type: "exact_fqn" | "exact_simple" | "class_contains" | "method" | "title" | "content"
        """
        if not self._available:
            return []

        candidates = self._query_candidates(name, entity_type)
        if not candidates:
            return []

        scored = self._score_and_rank(candidates, name)
        return scored

    def _query_candidates(
        self, name: str, entity_type: str | None,
    ) -> list[dict]:
        """从 SQLite FTS5 检索候选实体。"""
        conn = sqlite3.connect(self.db_path)
        try:
            # 用 FTS5 进行宽泛文本召回，特殊字符消毒
            fts_query = sanitize_fts5_query(name)
            sql = """
                SELECT doc_id, type, content, title, module, meta_json
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
            """
            params: list = [fts_query]

            if entity_type:
                sql += " AND type = ?"
                # 兼容两种 KnowledgeType 值（api / term）
                params.append(entity_type)

            sql += " LIMIT 50"
            rows = conn.execute(sql, params).fetchall()

            candidates: list[dict] = []
            for row in rows:
                doc_id, item_type, content, title, module, meta_json = row
                meta = {}
                if meta_json:
                    try:
                        meta = json.loads(meta_json)
                    except json.JSONDecodeError:
                        pass
                candidates.append({
                    "id": doc_id,
                    "type": item_type,
                    "content": content,
                    "title": title,
                    "module": module,
                    "meta": meta,
                })
            return candidates
        except Exception:
            logger.exception("entity search failed: name=%s", name)
            return []
        finally:
            conn.close()

    def _score_and_rank(
        self, candidates: list[dict], name: str,
    ) -> list[dict]:
        """按匹配质量评分并排序。

        score: 1.0=精确FQN, 0.95=精确简单类名, 0.85=类名包含, 0.8=方法名, 0.6=内容匹配
        """
        name_lower = name.lower()
        has_dot = "." in name
        # 拆分：最后一个点之前为 class_part，之后为 method_part
        if has_dot:
            class_part = name.rsplit(".", 1)[0]
            method_part = name.rsplit(".", 1)[1]
            class_part_lower = class_part.lower()
            method_part_lower = method_part.lower()

        scored: list[dict] = []
        for c in candidates:
            meta = c.get("meta", {})
            class_name = meta.get("class_name", "")
            method = meta.get("method", "")
            title = c.get("title", "")
            content = c.get("content", "")

            class_lower = class_name.lower()
            simple_class = class_name.rsplit(".", 1)[-1] if class_name else ""
            simple_lower = simple_class.lower()
            method_lower = method.lower()

            if has_dot:
                # "FileServiceManager.uploadFile" → 匹配类 + 方法
                class_score = _class_match_score(class_lower, simple_lower, class_part_lower)
                if class_score > 0 and method_part_lower == method_lower:
                    score = class_score  # 类 + 方法都匹配
                elif class_score > 0 and method_part_lower in method_lower:
                    score = class_score * 0.95  # 方法部分匹配
                elif class_score > 0:
                    score = class_score * 0.85  # 仅类匹配
                elif name_lower in method_lower:
                    score = 0.7
                elif name_lower in content.lower():
                    score = 0.5
                else:
                    continue
            else:
                # "FileServiceManager" 或 "generate" → 尝试匹配类名或方法名
                if simple_lower == name_lower:
                    score = 0.95  # 精确简单类名
                elif class_lower == name_lower:
                    score = 1.0  # 精确 FQN
                elif name_lower in class_lower:
                    score = 0.85  # 类名包含
                elif method_lower == name_lower:
                    score = 0.8  # 精确方法名
                elif name_lower in method_lower:
                    score = 0.7  # 方法名包含
                elif name_lower in title.lower():
                    score = 0.6
                elif name_lower in content.lower():
                    score = 0.5
                else:
                    continue

            scored.append({"chunk": c, "score": round(score, 4)})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored


def _class_match_score(class_lower: str, simple_lower: str, query_lower: str) -> float:
    """计算类名匹配分数。"""
    if class_lower == query_lower:
        return 1.0
    if simple_lower == query_lower:
        return 0.95
    if query_lower in class_lower:
        return 0.85
    return 0.0
