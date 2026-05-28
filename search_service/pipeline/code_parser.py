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


def parse_java_file(filepath: str, sdk_meta: dict, annotations: dict | None = None) -> list[dict]:
    """解析单个 .java 文件，提取所有 public 方法为知识 chunk。

    Args:
        filepath: .java 文件路径
        sdk_meta: {"group_id", "artifact_id", "version", "module_name"}
        annotations: .sdk-annotations.json 内容（可选）

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
    imports_map = _extract_imports(source)
    sdk_id = sdk_meta.get("artifact_id", "unknown")
    version = sdk_meta.get("version", "")
    class_stack: list[str] = [os.path.splitext(os.path.basename(filepath))[0]]

    # 第一遍：收集方法节点信息，用于角色推断
    method_nodes: list[tuple] = []
    constructor_nodes: list[tuple] = []
    for path, node in tree:
        if isinstance(node, javalang.tree.ClassDeclaration):
            class_stack.append(node.name)
        elif isinstance(node, javalang.tree.InterfaceDeclaration):
            class_stack.append(node.name)
        elif isinstance(node, javalang.tree.MethodDeclaration):
            # interface 方法默认 public，不显式包含 public modifier
            if "public" not in node.modifiers and not _in_interface(path):
                continue
            fqn = ".".join(class_stack[1:] + [node.name])
            method_nodes.append((node, fqn))
        elif isinstance(node, javalang.tree.ConstructorDeclaration):
            if "public" not in node.modifiers:
                continue
            fqn = ".".join(class_stack[1:])
            constructor_nodes.append((node, fqn))

    # 推断类的角色
    class_path = ".".join(class_stack[1:])
    role = _detect_class_role(package_name, class_path, method_nodes)
    leaf_pkg = package_name.lower().split(".")[-1] if package_name else ""
    class_fqn = f"{package_name}.{class_path}" if package_name else class_path

    # 构建压制信息
    suppressed_info = _build_suppressed_info(annotations or {}) if annotations else None

    # 第二遍：生成 chunk（含层级标注）
    for node, fqn in method_nodes:
        layer = _detect_method_layer(class_fqn, node.parameters, role, leaf_pkg, suppressed_info, package_name)
        ctx_deps = _detect_context_dependencies(node.parameters)
        ctx_provider = _detect_standard_context_provider(class_fqn, ctx_deps) if ctx_deps else None
        chunk = _method_to_chunk(
            node, package_name, fqn, sdk_id, version, filepath, javadoc_map, role,
            imports_map, layer, ctx_deps, ctx_provider,
        )
        chunks.append(chunk)

    for node, fqn in constructor_nodes:
        layer = _detect_method_layer(class_fqn, node.parameters, role, leaf_pkg, suppressed_info, package_name)
        ctx_deps = _detect_context_dependencies(node.parameters)
        ctx_provider = _detect_standard_context_provider(class_fqn, ctx_deps) if ctx_deps else None
        chunk = _constructor_to_chunk(
            node, package_name, fqn, sdk_id, version, filepath, javadoc_map, role,
            layer, ctx_deps, ctx_provider,
        )
        chunks.append(chunk)

    return chunks


def _detect_class_role(
    package_name: str,
    class_path: str,
    method_nodes: list[tuple],
) -> str:
    """根据包路径层级 + 类名 + 方法特征推断类在 SDK 中的角色。

    Returns:
        "entry_point" | "public_api" | "internal"
    """
    # 从 package_name 提取叶子包（如 com.company.file.dao → dao）
    pkg_parts = package_name.lower().split(".") if package_name else []
    leaf_pkg = pkg_parts[-1] if pkg_parts else ""

    # 子包分类
    sub_pkg_internal = {"dao", "config", "impl", "internal", "model", "dto", "vo"}
    sub_pkg_public = {"service", "api", "client", "facade"}

    is_root_pkg = leaf_pkg == "" or leaf_pkg == pkg_parts[0] if pkg_parts else True
    is_root = is_root_pkg or leaf_pkg not in (sub_pkg_internal | sub_pkg_public | {"util"})
    simple_name = class_path.split(".")[-1].lower()

    # 规则 1: 子包明确标记为内部（dao/config/impl/...）
    if leaf_pkg in sub_pkg_internal:
        return "internal"

    # 规则 2: 全是 getter/setter → internal
    if method_nodes and _all_getters_setters(method_nodes):
        return "internal"

    # 规则 3: 类名模式 → 偏向入口
    entry_suffixes = ("factory", "manager", "client", "bootstrap",
                       "starter", "builder")
    is_entry_name = simple_name.endswith(entry_suffixes)
    has_static_factory = _has_static_factory(method_nodes)
    has_self_return = _has_self_returning_methods(method_nodes, simple_name)

    # 3a: Builder 模式（含 build() 且方法返回自身）
    if has_self_return and _has_build_method(method_nodes):
        return "entry_point"

    # 3b: 根包 + 类名 Factory/Manager/Client/Bootstrap + 有公开非 getter 方法
    if is_entry_name and is_root and method_nodes:
        return "entry_point"

    # 3c: 含静态工厂 + 在根包或 public 子包
    if has_static_factory and (is_root or leaf_pkg in sub_pkg_public):
        return "entry_point"

    # 3d: 根包类名带入口后缀，即使无静态方法（可能是 Spring Bean 入口）
    if is_entry_name and leaf_pkg in sub_pkg_public:
        return "entry_point"

    # 规则 4: 在 service/api/client/facade 子包 → public_api
    if leaf_pkg in sub_pkg_public:
        return "public_api"

    # 规则 5: 根包 → public_api（接口、抽象类、枚举等）
    if is_root:
        return "public_api"

    # 规则 6: "util" 子包 → public_api（工具类属于公开能力）
    if leaf_pkg == "util":
        return "public_api"

    # 兜底
    return "internal"


# ── 层级标注（方案一） ──

# High-Level 类名后缀
_HIGH_LAYER_CLASS_SUFFIXES = (
    "manager", "facade", "gateway", "helper", "builder", "bootstrap", "starter",
)

# Low-Level 类名关键词
_LOW_LAYER_CLASS_KEYWORDS = ("impl", "internal")

# 复杂参数类型关键词（需要上下文构建）
_COMPLEX_PARAM_KEYWORDS = (
    "context", "request", "response", "session", "config",
    "configuration", "environment", "applicationcontext",
)

# 参数数量阈值：超过此值视为复杂 API
_MAX_HIGH_LAYER_PARAMS = 2


def _is_complex_param_type(type_name: str) -> bool:
    """判断参数类型是否需要上下文构建（复杂对象）。"""
    if not type_name:
        return False
    lower = type_name.lower()
    for kw in _COMPLEX_PARAM_KEYWORDS:
        if kw in lower:
            return True
    return False


def _detect_method_layer(
    class_name: str,
    params: list,
    class_role: str,
    leaf_pkg: str,
    suppressed_info: dict | None = None,
    package_name: str = "",
) -> str:
    """根据类名、参数复杂度、角色推断方法层级: high | mid | low | suppressed。

    entry_point 方法无条件 high——工厂/入口方法接收 Config 等复杂参数是正常模式。
    suppressed 最高优先级：标注的包/类直接压制，search 不可见但 entity 可查。
    """
    # 0) 压制层 —— 最高优先级，标注直接兜底
    if suppressed_info and _is_suppressed(package_name, class_name, suppressed_info):
        return "suppressed"

    simple_name = class_name.split(".")[-1].lower()

    # 1) Low-Level：类名含 Impl / Internal
    if any(kw in simple_name for kw in _LOW_LAYER_CLASS_KEYWORDS):
        return "low"

    # 2) entry_point 无条件 high
    if class_role == "entry_point":
        return "high"

    # 3) High-Level：类名含 Manager / Facade / Gateway / Helper 等
    is_high_class = simple_name.endswith(_HIGH_LAYER_CLASS_SUFFIXES)
    has_few_params = len(params) <= _MAX_HIGH_LAYER_PARAMS

    if is_high_class and has_few_params and class_role in ("entry_point", "public_api"):
        return "high"

    # 4) Low-Level：参数含复杂对象（Context / Request 等，entry 已排除）
    for p in params:
        type_name = p.type.name if p.type else ""
        if _is_complex_param_type(type_name):
            return "low"

    # 5) 子包辅助判断
    public_pkgs = {"service", "api", "client", "facade"}
    if leaf_pkg in public_pkgs and not is_high_class:
        return "mid"

    internal_pkgs = {"dao", "config", "impl", "internal", "model", "dto", "vo"}
    if leaf_pkg in internal_pkgs:
        return "low"

    # 6) 兜底
    return "mid"


def _detect_context_dependencies(params) -> list[str]:
    """检测方法参数中的上下文依赖类型。"""
    deps: list[str] = []
    for p in params:
        type_name = p.type.name if p.type else ""
        if _is_complex_param_type(type_name):
            deps.append(type_name)
    return deps


def _detect_standard_context_provider(
    class_name: str,
    context_deps: list[str],
) -> str | None:
    """推测标准上下文获取方式。

    基于类名模式推测——例如 FileQueryManager 暗示
    通过 ContextManager.getCurrentTenantId() 获取上下文。
    """
    if not context_deps:
        return None
    # 常见的上下文提供模式
    if any("Context" in d for d in context_deps):
        return "ContextManager.getCurrentTenantId()"
    if any("Request" in d for d in context_deps):
        simple = class_name.split(".")[-1]
        return f"{simple}.buildRequest(...)"
    return None


def _detect_construction_pattern(node, return_type: str) -> str:
    """检测方法是否为 SDK 实例的构造入口。

    Returns:
        "builder" | "static_factory" | "singleton" | "constructor" | ""
    """
    import javalang
    modifiers = node.modifiers if hasattr(node, "modifiers") else []
    is_static = "static" in modifiers

    if isinstance(node, javalang.tree.ConstructorDeclaration):
        return "constructor"

    if node.name in ("newBuilder", "builder") and is_static:
        return "builder"

    _PRIMITIVE = {"void", "int", "long", "float", "double", "boolean", "byte", "short", "char"}
    if is_static and return_type and return_type not in _PRIMITIVE:
        if node.name in ("getInstance", "getDefault", "getSingleton", "instance"):
            return "singleton"
        return "static_factory"

    return ""


def _all_getters_setters(method_nodes: list[tuple]) -> bool:
    """判断所有方法是否都是 getter/setter（isXxx / getXxx / setXxx）。"""
    if not method_nodes:
        return False
    import javalang
    non_static_count = 0
    for node, _ in method_nodes:
        if "static" in node.modifiers:
            continue  # 静态 getXxx() 是工厂方法，不是 getter
        non_static_count += 1
        name = node.name
        if not (name.startswith("get") or name.startswith("set")
                or name.startswith("is") or name == "toString"
                or name == "hashCode" or name == "equals"):
            return False
    return non_static_count > 0


def _has_static_factory(method_nodes: list[tuple]) -> bool:
    """判断类是否包含静态工厂方法——static + 返回类型非 void/非基本类型。"""
    primitive = {"void", "int", "long", "float", "double", "boolean", "byte", "short", "char"}
    for node, _ in method_nodes:
        if "static" in node.modifiers:
            rt = node.return_type.name if node.return_type else "void"
            if rt not in primitive and not rt.startswith("java.lang."):
                return True
    return False


def _has_self_returning_methods(method_nodes: list[tuple], class_simple_name: str) -> bool:
    """判断类是否有方法返回自身类型（Builder 模式特征）。"""
    for node, _ in method_nodes:
        rt = node.return_type.name if node.return_type else ""
        if rt == class_simple_name:
            return True
    return False


def _has_build_method(method_nodes: list[tuple]) -> bool:
    """判断类是否有 build() 方法（Builder 模式标志）。"""
    for node, _ in method_nodes:
        if node.name == "build":
            return True
    return False


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
    role: str = "public_api",
    imports_map: dict[str, str] | None = None,
    layer: str = "mid",
    context_deps: list[str] | None = None,
    context_provider: str | None = None,
) -> dict:
    params_str = _format_params(node.parameters)
    returns = node.return_type.name if node.return_type else "void"

    calls = _extract_calls(node)
    construction = _detect_construction_pattern(node, returns)

    # 完整限定名: package.ClassName.method → 即 import 路径
    fqn = f"{package_name}.{full_method_name}" if package_name else full_method_name
    class_fqn = fqn.rsplit(".", 1)[0]  # package.ClassName
    chunk_id = _make_id(f"{sdk_id}:{fqn}")

    # Javadoc
    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")

    # 返回值类型 import 解析
    imports = imports_map or {}
    return_type_import = _resolve_return_type_import(returns, imports, package_name)

    # content: import + 全限定调用 + 返回类型（消除幻觉）
    content_parts = [f"import {class_fqn};"]
    if return_type_import and return_type_import != class_fqn:
        content_parts.append(f"import {return_type_import};")
    content_parts.append(
        f"{class_fqn.split('.')[-1]}.{node.name}({params_str}) → {returns}"
    )
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
        "role": role,
        "layer": layer,
        "requires_context_building": bool(context_deps),
    }
    if return_type_import:
        meta["return_type_import"] = return_type_import
    if construction:
        meta["construction_pattern"] = construction
    if context_deps:
        meta["context_dependencies"] = context_deps
    if context_provider:
        meta["standard_context_provider"] = context_provider
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
    role: str = "public_api",
    layer: str = "mid",
    context_deps: list[str] | None = None,
    context_provider: str | None = None,
) -> dict:
    """class_path: 包内类路径，如 "sdk.PointsException" 或 "PointsException" """
    params_str = _format_params(node.parameters)
    class_simple = class_path.split(".")[-1]

    fqn = f"{package_name}.{class_path}" if package_name else class_path
    chunk_id = _make_id(f"{sdk_id}:{fqn}:constructor")

    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")

    content_parts = [
        f"import {fqn};",
        f"new {class_simple}({params_str})",
    ]
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
        "role": role,
        "layer": layer,
        "requires_context_building": bool(context_deps),
        "construction_pattern": "constructor",
    }
    if context_deps:
        meta["context_dependencies"] = context_deps
    if context_provider:
        meta["standard_context_provider"] = context_provider
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


def _in_interface(path: list) -> bool:
    """检查节点路径中是否包含 InterfaceDeclaration（即方法声明在 interface 内部）。"""
    import javalang
    return any(isinstance(p, javalang.tree.InterfaceDeclaration) for p in path)


# java.lang 类型和原始类型，不需要 import
_JAVA_LANG_TYPES: set[str] = {
    "String", "Object", "Integer", "Long", "Float", "Double",
    "Boolean", "Byte", "Short", "Character", "Number", "Class",
    "void", "int", "long", "float", "double", "boolean", "byte", "short", "char",
}
_IMPORT_PATTERN = re.compile(
    r"^import\s+(?:static\s+)?([\w.]+(?:\.[\w.]+)*(?:\.\*)?)\s*;",
    re.MULTILINE,
)
_GENERIC_PATTERN = re.compile(r"<\s*(\w+(?:\.\w+)*)\s*>")  # 提取最内层泛型参数


def _extract_imports(source: str) -> dict[str, str]:
    """从 Java 源码提取 import 语句，构建 {simple_name: full_path} 映射。

    - 常规 import: import com.example.Foo → {"Foo": "com.example.Foo"}
    - 通配 import: import com.example.* → {"*": "com.example"}（兜底猜测用）
    - 静态 import: import static ... → 跳过（方法导入，非类型）
    - 内部类: import com.example.Foo.Bar → {"Bar": "com.example.Foo.Bar"}
    """
    imports: dict[str, str] = {}
    for m in _IMPORT_PATTERN.finditer(source):
        raw = m.group(1)
        if raw.endswith(".*"):
            imports.setdefault("*", raw[:-2])  # 多个通配时保留第一个
        else:
            simple = raw.rsplit(".", 1)[-1].split(".")[-1]
            imports[simple] = raw
    return imports


def _extract_generic_type(return_type: str) -> str:
    """提取泛型的类型参数。List<FileService> → FileService，List<List<Foo>> → Foo。"""
    if not return_type:
        return ""
    # 去掉所有泛型层: 迭代匹配直到没有 <>
    prev = ""
    while prev != return_type:
        prev = return_type
        m = _GENERIC_PATTERN.search(return_type)
        if m:
            return_type = m.group(1)
    return return_type.strip()


def _resolve_return_type_import(
    return_type: str,
    imports_map: dict[str, str],
    package_name: str,
) -> str | None:
    """根据 import 映射和包名解析返回值类型的完整路径。

    Returns:
        完整 import 路径，或 None（无需 import 或无法解析）。
    """
    if not return_type or return_type in _JAVA_LANG_TYPES:
        return None
    if return_type.startswith("java.lang."):
        return None

    # 泛型提取: List<FileService> → FileService
    simple = _extract_generic_type(return_type)
    if simple in _JAVA_LANG_TYPES or simple.startswith("java.lang."):
        return None

    # 1) import 映射精确匹配
    if simple in imports_map:
        return imports_map[simple]

    # 2) 同包类型
    if package_name:
        return f"{package_name}.{simple}"

    return None


def _load_annotations(repo_dir: str) -> dict:
    """加载 .sdk-annotations.json 标注文件。文件不存在时返回 {}。"""
    path = os.path.join(repo_dir, ".sdk-annotations.json")
    if not os.path.exists(path):
        return {}
    try:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            logger.info("loaded annotations from %s", path)
            return data
    except Exception:
        logger.warning("failed to read .sdk-annotations.json: %s", path)
    return {}


def _build_suppressed_info(annotations: dict) -> dict | None:
    """从标注中提取 suppressed 信息，构建快速查表结构。"""
    suppressed = annotations.get("suppressed")
    if not suppressed:
        return None
    return {
        "packages": suppressed.get("packages", []),
        "classes": set(suppressed.get("classes", [])),
    }


def _is_suppressed(package_name: str, class_fqn: str, info: dict) -> bool:
    """检查类是否被标注为 suppressed。"""
    for pkg_prefix in info.get("packages", []):
        if package_name.startswith(pkg_prefix):
            return True
    return class_fqn in info.get("classes", [])


def parse_java_repo(repo_dir: str) -> list[dict]:
    """解析 Java 仓库：自动发现 pom 模块 + 提取所有 public 方法。

    Returns:
        统一格式的知识 chunk 列表，可直接喂给 orchestrator 索引。
    """
    modules = discover_pom_modules(repo_dir)
    annotations = _load_annotations(repo_dir)
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
        # 过滤 test 目录（src/test/java）
        src_files = [jf for jf in java_files if "/test/" not in str(jf).replace("\\", "/")]
        skipped = len(java_files) - len(src_files)
        if skipped:
            logger.info("module %s: %d java files (%d test skipped)", mod.get("artifact_id"), len(java_files), skipped)
        else:
            logger.info("module %s: %d java files", mod.get("artifact_id"), len(java_files))

        for jf in src_files:
            chunks = parse_java_file(str(jf), mod, annotations)
            all_chunks.extend(chunks)

    logger.info("total java chunks: %d", len(all_chunks))
    return all_chunks
