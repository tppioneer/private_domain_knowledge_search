"""Pipeline 数据模型 —— 独立于 search_service/schemas.py，允许代码重复。

pipeline 阶段只用这些类型，不依赖 search_service 任何代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ChunkMeta:
    """Chunk 的结构化元数据，由 code_parser 或 chunker 写入。"""
    knowledge_source: str = ""      # "sdk_code" | "doc"
    sdk: str = ""                   # artifact_id
    version: str = ""
    class_name: str = ""
    method: str = ""
    return_type: str = ""
    return_type_import: str = ""
    calls: list[str] = field(default_factory=list)
    role: str = ""                  # entry_point | public_api | internal
    layer: str = ""                 # high | mid | low | suppressed
    construction_pattern: str = ""  # builder | static_factory | singleton | constructor
    requires_context_building: bool = False
    context_dependencies: list[str] = field(default_factory=list)
    standard_context_provider: str = ""
    source: str = ""                # 文档来源路径
    applicable_version: str = ""
    related_ticket: str = ""

    def to_dict(self) -> dict:
        d = {}
        for f in self.__dataclass_fields__:
            v = getattr(self, f)
            if v or (isinstance(v, bool) and v is True):
                d[f] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class PipelineChunk:
    """Pipeline 内部统一 chunk 格式。"""
    id: str
    type: str = "api"               # api | document | best_practice | ...
    content: str = ""
    title: str = ""
    module: str = ""
    source_path: str = ""
    meta: ChunkMeta = field(default_factory=ChunkMeta)

    def to_index_dict(self) -> dict:
        """转换为索引写入格式（兼容 BM25 / FAISS / Graph 的输入）。"""
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "title": self.title,
            "module": self.module,
            "source_path": self.source_path,
            "meta_json": self.meta.to_json(),
        }


@dataclass
class FileRecord:
    """loader 加载的文件记录。"""
    path: str
    filename: str
    content: str
