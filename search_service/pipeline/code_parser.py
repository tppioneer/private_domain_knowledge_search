"""Java SDK 源码 AST 解析 —— 提取 pom.xml 坐标 + public 方法签名 + 调用链。

支持多模块 Maven 项目：遍历子目录中的 pom.xml，每个模块独立提取坐标。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

_MAVEN_NS = "http://maven.apache.org/POM/4.0.0"

# 版本占位符模式 —— 这些不能作为真实版本写入 meta
_VERSION_PLACEHOLDER = re.compile(
    r"^\$\{.*\}"           # ${revision} 等 Maven 属性
    r"|^.*-SNAPSHOT$"      # 1.0.0-SNAPSHOT
    r"|^$",                 # 空字符串
    re.IGNORECASE,
)


def extract_pom_info(pom_path: str) -> dict | None:
    """解析单个 pom.xml，提取 Maven 坐标。

    Returns:
        {"groupId": "...", "artifactId": "...", "version": "..."} 或 None
    """
    try:
        tree = ET.parse(pom_path)
        root = tree.getroot()

        ns_match = re.match(r"\{(.+)\}", root.tag)
        ns = ns_match.group(1) if ns_match else _MAVEN_NS

        def find_text(tag: str, default: str = "") -> str:
            el = root.find(f"{{{ns}}}{tag}")
            return el.text.strip() if el is not None and el.text else default

        group_id = find_text("groupId")
        if not group_id:
            parent = root.find(f"{{{ns}}}parent")
            if parent is not None:
                g = parent.find(f"{{{ns}}}groupId")
                group_id = g.text.strip() if g is not None and g.text else ""

        artifact_id = find_text("artifactId")
        version = find_text("version")
        if not version:
            parent = root.find(f"{{{ns}}}parent")
            if parent is not None:
                v = parent.find(f"{{{ns}}}version")
                version = v.text.strip() if v is not None and v.text else ""

        if not artifact_id:
            logger.warning("pom.xml missing artifactId: %s", pom_path)
            return None

        return {
            "group_id": group_id or "",
            "artifact_id": artifact_id,
            "version": version or "",
        }
    except Exception:
        logger.exception("failed to parse pom.xml: %s", pom_path)
        return None


def _is_placeholder_version(version: str) -> bool:
    return bool(_VERSION_PLACEHOLDER.match(version))


def _load_version_overrides(repo_dir: str) -> dict[str, str]:
    """加载 .sdk-versions.json 作为版本覆盖表。

    格式: {"artifact_id": "real_version", ...}
    """
    override_path = os.path.join(repo_dir, ".sdk-versions.json")
    if not os.path.exists(override_path):
        return {}
    try:
        import json
        with open(override_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            logger.info("loaded version overrides: %d entries from %s", len(data), override_path)
            return data
    except Exception:
        logger.warning("failed to read .sdk-versions.json: %s", override_path)
    return {}


def _resolve_version(artifact_id: str, pom_version: str, overrides: dict[str, str]) -> str:
    """版本解析链: .sdk-versions.json → pom.xml 字面量 → 空字符串。

    Returns:
        解析后的版本字符串，占位符返回空字符串。
    """
    if artifact_id in overrides:
        return overrides[artifact_id]
    if pom_version and not _is_placeholder_version(pom_version):
        return pom_version
    if _is_placeholder_version(pom_version):
        logger.info(
            "version placeholder detected for %s: %s, resolved to empty",
            artifact_id, pom_version,
        )
    return ""


def discover_pom_modules(repo_dir: str) -> list[dict]:
    """扫描仓库目录，找到所有 pom.xml 并提取坐标。

    按深度排序：根 pom 在前，子模块在后。
    版本解析链: .sdk-versions.json > pom.xml 字面量 > 空字符串（兜底）。
    """
    modules: list[dict] = []
    root = Path(repo_dir)
    overrides = _load_version_overrides(repo_dir)

    pom_files = sorted(root.rglob("pom.xml"), key=lambda p: len(p.relative_to(root).parts))

    for pom_path in pom_files:
        info = extract_pom_info(str(pom_path))
        if info is None:
            continue
        # 版本解析：pom 继承前先检查覆盖文件
        info["version"] = _resolve_version(info["artifact_id"], info["version"], overrides)
        info["pom_dir"] = str(pom_path.parent)
        info["module_name"] = info["artifact_id"]
        modules.append(info)

    # 版本继承：子模块 version 为空时从父 pom 继承（仅当父 pom 有非占位符版本）
    if modules:
        root_version = modules[0].get("version", "")
        if root_version:
            for m in modules[1:]:
                if not m.get("version"):
                    m["version"] = root_version

    return modules


def parse_java_file(filepath: str, sdk_meta: dict) -> list[dict]:
    """解析单个 .java 文件，提取所有 public 方法为知识 chunk。

    Args:
        filepath: .java 文件路径
        sdk_meta: {"group_id", "artifact_id", "version", "module_name"}

    Returns:
        [{id, type, content, title, module, source_path, meta_json, calls[]}]
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except Exception:
        logger.warning("cannot read java file: %s", filepath)
        return []

    try:
        import javalang
        tree = javalang.parse.parse(source)
    except Exception:
        logger.warning("javalang parse failed: %s (possibly incomplete source)", filepath)
        return []

    chunks: list[dict] = []

    package_name = _extract_package(tree)
    javadoc_map = _extract_javadocs(source)
    sdk_id = sdk_meta.get("artifact_id", "unknown")
    version = sdk_meta.get("version", "")
    # 追踪类层级以构造完整限定名（处理嵌套类）
    class_stack: list[str] = [os.path.splitext(os.path.basename(filepath))[0]]

    for path, node in tree:
        if isinstance(node, javalang.tree.ClassDeclaration):
            class_stack.append(node.name)
        elif isinstance(node, javalang.tree.MethodDeclaration):
            if "public" not in node.modifiers:
                continue
            fqn = ".".join(class_stack[1:] + [node.name])  # 跳过默认值
            chunk = _method_to_chunk(
                node, package_name, fqn, sdk_id, version, filepath, javadoc_map,
            )
            chunks.append(chunk)
        elif isinstance(node, javalang.tree.ConstructorDeclaration):
            if "public" not in node.modifiers:
                continue
            fqn = ".".join(class_stack[1:])  # 构造函数的 FQN = 类的完整限定名
            chunk = _constructor_to_chunk(
                node, package_name, fqn, sdk_id, version, filepath, javadoc_map,
            )
            chunks.append(chunk)

    return chunks


def _extract_javadocs(source: str) -> dict[int, str]:
    """从源码中提取每个 public 方法行号 → Javadoc 文本的映射。

    预处理：javadoc 注释 `/** ... */` 紧邻 public 方法的，按方法起始行号索引。
    """
    javadoc_map: dict[int, str] = {}
    # 匹配 /** ... */ 注释块（支持多行）
    doc_pattern = re.compile(r"/\*\*([^*]|\*(?!/))*?\*/", re.DOTALL)
    # 匹配 public 方法/构造函数定义行
    method_pattern = re.compile(
        r"^\s*public\s+(?:static\s+)?(?:[\w<>\[\],\s]+\s+)?(\w+)\s*\(",
        re.MULTILINE,
    )

    line_starts = {m.start(): m for m in method_pattern.finditer(source)}
    doc_matches = [(m.end(), m.group()) for m in doc_pattern.finditer(source)]

    for doc_end, doc_text in doc_matches:
        # 找紧随注释后的 public 方法
        best_start = None
        for start in line_starts:
            if start >= doc_end:
                if best_start is None or start < best_start:
                    best_start = start
        if best_start is not None:
            line_no = source[:best_start].count("\n") + 1
            javadoc_map[line_no] = _clean_javadoc(doc_text)

    return javadoc_map


def _clean_javadoc(doc: str) -> str:
    """清洗 Javadoc 注释：去掉 /** */ 标记和每行的 * 前缀。"""
    lines = doc.strip().split("\n")
    cleaned: list[str] = []
    for line in lines:
        line = line.strip()
        if line.startswith("/**"):
            line = line[3:].strip()
        elif line.startswith("*/"):
            continue
        if line.startswith("* "):
            line = line[2:]
        elif line.startswith("*"):
            line = line[1:]
        if line or cleaned:  # 保留内部空行
            cleaned.append(line)
    # 去掉开头和结尾空行
    while cleaned and not cleaned[0]:
        cleaned.pop(0)
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return "\n".join(cleaned)


def _extract_package(tree) -> str:
    import javalang
    if tree.package:
        return tree.package.name
    for path, node in tree:
        if isinstance(node, javalang.tree.PackageDeclaration):
            return node.name
    return ""


def _method_to_chunk(
    node, package_name: str, full_method_name: str,
    sdk_id: str, version: str, filepath: str, javadoc_map: dict[int, str],
) -> dict:
    params = _format_params(node.parameters)
    returns = node.return_type.name if node.return_type else "void"
    signature = f"public {returns} {node.name}({params})"

    calls = _extract_calls(node)

    # 完整限定名: package.ClassName.method → 即 import 路径
    fqn = f"{package_name}.{full_method_name}" if package_name else full_method_name
    class_fqn = fqn.rsplit(".", 1)[0]  # package.ClassName
    chunk_id = _make_id(f"{sdk_id}:{fqn}")

    # Javadoc
    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")

    content_parts = [signature]
    if javadoc:
        content_parts.append("")
        content_parts.append(javadoc)

    title_parts = full_method_name.rsplit(".", 1)
    title = title_parts[-2] + "." + node.name if len(title_parts) == 2 else node.name

    meta = {
        "knowledge_source": "sdk_code",
        "sdk": sdk_id,
        "version": version,
        "class_name": class_fqn,
        "method": node.name,
        "return_type": returns,
        "calls": calls,
    }
    import json
    meta_json = json.dumps(meta, ensure_ascii=False)

    return {
        "id": chunk_id,
        "type": "api",
        "content": "\n".join(content_parts),
        "title": title,
        "module": sdk_id,
        "source_path": filepath,
        "meta_json": meta_json,
    }


def _constructor_to_chunk(
    node, package_name: str, class_path: str,
    sdk_id: str, version: str, filepath: str, javadoc_map: dict[int, str],
) -> dict:
    """class_path: 包内类路径，如 "sdk.PointsException" 或 "PointsException" """
    params = _format_params(node.parameters)
    class_simple = class_path.split(".")[-1]
    signature = f"public {class_simple}({params})"

    fqn = f"{package_name}.{class_path}" if package_name else class_path
    chunk_id = _make_id(f"{sdk_id}:{fqn}:constructor")

    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")

    content_parts = [signature]
    if javadoc:
        content_parts.append("")
        content_parts.append(javadoc)

    meta = {
        "knowledge_source": "sdk_code",
        "sdk": sdk_id,
        "version": version,
        "class_name": fqn,
        "method": class_simple,
        "return_type": class_simple,
    }
    import json
    meta_json = json.dumps(meta, ensure_ascii=False)

    return {
        "id": chunk_id,
        "type": "api",
        "content": "\n".join(content_parts),
        "title": f"{class_simple}.constructor",
        "module": sdk_id,
        "source_path": filepath,
        "meta_json": meta_json,
    }


def _format_params(params) -> str:
    if not params:
        return ""
    parts = []
    for p in params:
        type_name = p.type.name if p.type else "Object"
        parts.append(f"{type_name} {p.name}")
    return ", ".join(parts)


def _extract_calls(method_node) -> list[str]:
    """提取方法体内调用的方法名列表。"""
    import javalang
    calls: list[str] = []
    for _, node in method_node:
        if isinstance(node, javalang.tree.MethodInvocation):
            calls.append(node.member)
    return calls


def _make_id(raw: str) -> str:
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def parse_java_repo(repo_dir: str) -> list[dict]:
    """解析 Java 仓库：自动发现 pom 模块 + 提取所有 public 方法。

    Returns:
        统一格式的知识 chunk 列表，可直接喂给 orchestrator 索引。
    """
    modules = discover_pom_modules(repo_dir)
    if not modules:
        logger.warning("no pom.xml found in %s, trying without SDK metadata", repo_dir)
        modules = [{
            "group_id": "", "artifact_id": os.path.basename(repo_dir),
            "version": "", "module_name": os.path.basename(repo_dir),
            "pom_dir": repo_dir,
        }]

    all_chunks: list[dict] = []
    for mod in modules:
        pom_dir = mod.get("pom_dir", repo_dir)
        java_files = list(Path(pom_dir).rglob("*.java"))
        logger.info("module %s: %d java files", mod.get("artifact_id"), len(java_files))

        for jf in java_files:
            chunks = parse_java_file(str(jf), mod)
            all_chunks.extend(chunks)

    logger.info("total java chunks: %d", len(all_chunks))
    return all_chunks
