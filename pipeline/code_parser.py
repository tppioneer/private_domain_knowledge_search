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


def _discover_gradle_modules(repo_dir: str) -> list[dict]:
    """从 Gradle 项目提取模块信息（build.gradle / build.gradle.kts）。

    按深度排序，模块名从目录名推断。
    """
    modules: list[dict] = []
    root = Path(repo_dir)
    gradle_files = sorted(
        list(root.rglob("build.gradle")) + list(root.rglob("build.gradle.kts")),
        key=lambda p: len(p.relative_to(root).parts),
    )
    for gf in gradle_files:
        module_dir = str(gf.parent)
        # 跳过根目录本身（settings.gradle 存在的目录）
        modules.append({
            "group_id": "",
            "artifact_id": os.path.basename(module_dir),
            "version": "",
            "pom_dir": module_dir,
            "module_name": os.path.basename(module_dir),
        })
    return modules


def _discover_flat_module(repo_dir: str) -> list[dict]:
    """无构建系统的纯目录——整个目录作为一个模块。"""
    return [{
        "group_id": "",
        "artifact_id": os.path.basename(repo_dir.rstrip("/\\")),
        "version": "",
        "pom_dir": repo_dir,
        "module_name": os.path.basename(repo_dir.rstrip("/\\")),
    }]


def discover_modules(repo_dir: str) -> list[dict]:
    """自动检测项目类型，发现所有代码模块。

    检测优先级: Maven (pom.xml) > Gradle (build.gradle) > 纯目录。
    """
    maven = discover_pom_modules(repo_dir)
    if maven:
        logger.info("detected Maven project: %d modules", len(maven))
        return maven

    gradle = _discover_gradle_modules(repo_dir)
    if gradle:
        logger.info("detected Gradle project: %d modules", len(gradle))
        return gradle

    logger.info("no build system detected, treating as flat directory")
    return _discover_flat_module(repo_dir)


def parse_java_file(filepath: str, sdk_meta: dict, annotations: dict | None = None, rules: dict | None = None) -> list:
    """解析单个 .java 文件，提取所有 public 方法为知识 chunk。

    Args:
        filepath: .java 文件路径
        sdk_meta: {"group_id", "artifact_id", "version", "module_name"}
        annotations: .sdk-annotations.json 内容（可选，模块级）
        rules: 合并后的 pipeline 规则（可选）

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
        from .adapters import parse_source, detect_language
        lang = detect_language(filepath)
        tree = parse_source(lang, source)
    except Exception:
        logger.warning("parse failed: %s (possibly incomplete source)", filepath)
        return []

    chunks: list = []

    package_name = _extract_package(tree)
    javadoc_map = _extract_javadocs(source)
    imports_map = _extract_imports(source)
    sdk_id = sdk_meta.get("artifact_id", "unknown")
    version = sdk_meta.get("version", "")
    class_stack: list[str] = [os.path.splitext(os.path.basename(filepath))[0]]

    # 第一遍：收集方法节点信息，用于角色推断
    method_nodes: list[tuple] = []
    constructor_nodes: list[tuple] = []
    from .adapters.base import _ClassDecl, _InterfaceDecl, _MethodDecl, _ConstructorDecl
    for path, node in tree:
        if isinstance(node, _ClassDecl):
            class_stack.append(node.name)
        elif isinstance(node, _InterfaceDecl):
            class_stack.append(node.name)
        elif isinstance(node, _MethodDecl):
            if "public" not in node.modifiers and not _in_interface(path):
                continue
            fqn = ".".join(class_stack[1:] + [node.name])
            method_nodes.append((node, fqn))
        elif isinstance(node, _ConstructorDecl):
            if "public" not in node.modifiers:
                continue
            fqn = ".".join(class_stack[1:])
            constructor_nodes.append((node, fqn))

    # 推断类的角色
    class_path = ".".join(class_stack[1:])
    _default = _load_default_rules()
    role_rules = (rules or {}).get("role_rules", _default.get("role_rules", {}))
    layer_rules = (rules or {}).get("layer_rules", _default.get("layer_rules", {}))
    role = _detect_class_role(package_name, class_path, method_nodes, role_rules, annotations)
    leaf_pkg = package_name.lower().split(".")[-1] if package_name else ""
    class_fqn = f"{package_name}.{class_path}" if package_name else class_path

    # 构建压制信息
    suppressed_info = _build_suppressed_info(annotations or {}) if annotations else None
    cp_keywords = tuple(layer_rules.get("complex_param_keywords", []))

    # 第二遍：生成 chunk（含层级标注）
    for node, fqn in method_nodes:
        layer = _detect_method_layer(class_fqn, node.parameters, role, leaf_pkg, suppressed_info, package_name, layer_rules)
        ctx_deps = _detect_context_dependencies(node.parameters, cp_keywords)
        ctx_provider = _detect_standard_context_provider(class_fqn, ctx_deps) if ctx_deps else None
        chunk = _method_to_chunk(
            node, package_name, fqn, sdk_id, version, filepath, javadoc_map, role,
            imports_map, layer, ctx_deps, ctx_provider,
        )
        chunks.append(chunk)

    for node, fqn in constructor_nodes:
        layer = _detect_method_layer(class_fqn, node.parameters, role, leaf_pkg, suppressed_info, package_name, layer_rules)
        ctx_deps = _detect_context_dependencies(node.parameters, cp_keywords)
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
    role_rules: dict | None = None,
    annotations: dict | None = None,
) -> str:
    """根据包路径层级 + 类名 + 方法特征推断类在 SDK 中的角色。

    标注的 entry_points 优先级高于启发式推断。
    """
    rules = role_rules or _load_default_rules().get("role_rules", {})
    sub_pkg_internal = set(rules.get("sub_pkg_internal", []))
    sub_pkg_public = set(rules.get("sub_pkg_public", []))
    entry_suffixes = tuple(rules.get("entry_suffixes", []))

    # 从 package_name 提取叶子包（如 com.company.file.dao → dao）
    pkg_parts = package_name.lower().split(".") if package_name else []
    leaf_pkg = pkg_parts[-1] if pkg_parts else ""

    is_root_pkg = leaf_pkg == "" or leaf_pkg == pkg_parts[0] if pkg_parts else True
    is_root = is_root_pkg or leaf_pkg not in (sub_pkg_internal | sub_pkg_public | {"util"})
    simple_name = class_path.split(".")[-1].lower()
    class_fqn = f"{package_name}.{class_path}" if package_name else class_path

    # 标注覆盖：声明的 entry_points 无条件生效
    if annotations:
        entry_list = annotations.get("entry_points", [])
        if class_fqn in entry_list:
            return "entry_point"

    # 规则 1: 子包明确标记为内部
    if leaf_pkg in sub_pkg_internal:
        return "internal"

    # 规则 2: 全是 getter/setter → internal
    if method_nodes and _all_getters_setters(method_nodes):
        return "internal"

    # 规则 3: 类名模式 → 偏向入口
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


# ── 默认配置目录（pipeline/config/） ──

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def _load_default_rules() -> dict:
    """加载 pipeline/config/default_rules.json 作为全局默认规则。"""
    path = os.path.join(_CONFIG_DIR, "default_rules.json")
    return _load_one_json(path) or {}


def _load_default_annotations() -> dict:
    """加载 pipeline/config/default_annotations.json 作为全局默认标注。"""
    path = os.path.join(_CONFIG_DIR, "default_annotations.json")
    return _load_one_json(path) or {}


def _is_complex_param_type(type_name: str, keywords: tuple = ()) -> bool:
    """判断参数类型是否需要上下文构建（复杂对象）。"""
    if not type_name:
        return False
    lower = type_name.lower()
    for kw in keywords:
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
    layer_rules: dict | None = None,
) -> str:
    """根据类名、参数复杂度、角色推断方法层级: high | mid | low | suppressed。

    entry_point 方法无条件 high——工厂/入口方法接收 Config 等复杂参数是正常模式。
    suppressed 最高优先级：标注的包/类直接压制，search 不可见但 entity 可查。
    """
    _default = _load_default_rules()
    rules = layer_rules or _default.get("layer_rules", {})
    high_suffixes = tuple(rules.get("high_layer_suffixes", []))
    low_keywords = tuple(rules.get("low_layer_keywords", []))
    complex_keywords = tuple(rules.get("complex_param_keywords", []))
    max_params = rules.get("max_high_layer_params", 2)
    internal_pkgs = set(_default.get("role_rules", {}).get("sub_pkg_internal", []))
    public_pkgs = set(_default.get("role_rules", {}).get("sub_pkg_public", []))

    # 0) 压制层 —— 最高优先级，标注直接兜底
    if suppressed_info and _is_suppressed(package_name, class_name, suppressed_info):
        return "suppressed"

    simple_name = class_name.split(".")[-1].lower()

    # 1) Low-Level：类名含 low layer 关键词
    if any(kw in simple_name for kw in low_keywords):
        return "low"

    # 2) entry_point 无条件 high
    if class_role == "entry_point":
        return "high"

    # 3) High-Level：类名含 high layer 后缀
    is_high_class = simple_name.endswith(high_suffixes) if high_suffixes else False
    has_few_params = len(params) <= max_params

    if is_high_class and has_few_params and class_role in ("entry_point", "public_api"):
        return "high"

    # 4) Low-Level：参数含复杂对象（entry 已排除）
    for p in params:
        type_name = p.type.name if p.type else ""
        if _is_complex_param_type(type_name, complex_keywords):
            return "low"

    # 5) 子包辅助判断
    if leaf_pkg in public_pkgs and not is_high_class:
        return "mid"

    if leaf_pkg in internal_pkgs:
        return "low"

    # 6) 兜底
    return "mid"


def _detect_context_dependencies(params, keywords: tuple = ()) -> list[str]:
    """检测方法参数中的上下文依赖类型。"""
    deps: list[str] = []
    for p in params:
        type_name = p.type.name if p.type else ""
        if _is_complex_param_type(type_name, keywords):
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
    from .adapters.base import _ConstructorDecl
    modifiers = node.modifiers if hasattr(node, "modifiers") else []
    is_static = "static" in modifiers

    if isinstance(node, _ConstructorDecl):
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
    if tree.package:
        return tree.package.name
    return ""


def _method_to_chunk(
    node, package_name: str, full_method_name: str,
    sdk_id: str, version: str, filepath: str, javadoc_map: dict[int, str],
    role: str = "public_api",
    imports_map: dict[str, str] | None = None,
    layer: str = "mid",
    context_deps: list[str] | None = None,
    context_provider: str | None = None,
) -> PipelineChunk:
    from .models import ChunkMeta, PipelineChunk
    params_str = _format_params(node.parameters)
    returns = node.return_type.name if node.return_type else "void"
    calls = _extract_calls(node)
    construction = _detect_construction_pattern(node, returns)

    fqn = f"{package_name}.{full_method_name}" if package_name else full_method_name
    class_fqn = fqn.rsplit(".", 1)[0]
    chunk_id = _make_id(f"{sdk_id}:{fqn}")

    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")

    imports = imports_map or {}
    return_type_import = _resolve_return_type_import(returns, imports, package_name)

    content_parts = [f"import {class_fqn};"]
    if return_type_import and return_type_import != class_fqn:
        content_parts.append(f"import {return_type_import};")
    content_parts.append(f"{class_fqn.split('.')[-1]}.{node.name}({params_str}) → {returns}")
    if javadoc:
        content_parts.append("")
        content_parts.append(javadoc)

    title_parts = full_method_name.rsplit(".", 1)
    title = title_parts[-2] + "." + node.name if len(title_parts) == 2 else node.name

    meta = ChunkMeta(
        knowledge_source="sdk_code", sdk=sdk_id, version=version,
        class_name=class_fqn, method=node.name, return_type=returns,
        calls=calls, role=role, layer=layer,
        requires_context_building=bool(context_deps),
        return_type_import=return_type_import or "",
        construction_pattern=construction,
        context_dependencies=context_deps or [],
        standard_context_provider=context_provider or "",
    )

    return PipelineChunk(
        id=chunk_id, type="api", content="\n".join(content_parts),
        title=title, module=sdk_id, source_path=filepath, meta=meta,
    )


def _constructor_to_chunk(
    node, package_name: str, class_path: str,
    sdk_id: str, version: str, filepath: str, javadoc_map: dict[int, str],
    role: str = "public_api",
    layer: str = "mid",
    context_deps: list[str] | None = None,
    context_provider: str | None = None,
) -> PipelineChunk:
    from .models import ChunkMeta, PipelineChunk
    params_str = _format_params(node.parameters)
    class_simple = class_path.split(".")[-1]
    fqn = f"{package_name}.{class_path}" if package_name else class_path
    chunk_id = _make_id(f"{sdk_id}:{fqn}:constructor")

    line_no = node.position.line if node.position else 0
    javadoc = javadoc_map.get(line_no, "")
    content_parts = [f"import {fqn};", f"new {class_simple}({params_str})"]
    if javadoc:
        content_parts.append("")
        content_parts.append(javadoc)

    meta = ChunkMeta(
        knowledge_source="sdk_code", sdk=sdk_id, version=version,
        class_name=fqn, method=class_simple, return_type=class_simple,
        role=role, layer=layer, construction_pattern="constructor",
        requires_context_building=bool(context_deps),
        context_dependencies=context_deps or [],
        standard_context_provider=context_provider or "",
    )

    return PipelineChunk(
        id=chunk_id, type="api", content="\n".join(content_parts),
        title=f"{class_simple}.constructor", module=sdk_id, source_path=filepath, meta=meta,
    )


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
    from .adapters import extract_calls as _ext_calls
    return _ext_calls("java", method_node)  # 默认 Java，后续按语言分发


def _make_id(raw: str) -> str:
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _in_interface(path: list) -> bool:
    """检查节点路径中是否包含 InterfaceDeclaration（即方法声明在 interface 内部）。"""
    from .adapters.base import _InterfaceDecl
    return any(isinstance(p, _InterfaceDecl) for p in path)


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


def _load_one_json(path: str) -> dict:
    """安全加载单个 JSON 文件，失败返回 {}。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.warning("failed to read: %s", path)
        return {}


def _load_annotations(module_dir: str, repo_dir: str = "") -> dict:
    """加载 .sdk-annotations.json —— override 策略，就近覆盖。

    查找链：模块级(pom_dir) → 项目级(repo_dir) → pipeline/config/default_annotations.json
    不跨模块合并，每个模块的 entry_points/suppressed 互不污染。
    """
    candidates = [os.path.join(module_dir, ".sdk-annotations.json")]
    if repo_dir and repo_dir != module_dir:
        candidates.append(os.path.join(repo_dir, ".sdk-annotations.json"))
    for path in candidates:
        if not os.path.exists(path):
            continue
        data = _load_one_json(path)
        if data:
            logger.info("loaded annotations from %s", path)
            return data
    return _load_default_annotations()


def _load_rules(repo_dir: str) -> dict:
    """递归加载所有 .sdk-rules.json 并 merge 到全局默认规则上。

    合并策略：list 字段取并集，scalar 字段覆盖。
    查找范围：repo_dir 下的所有 .sdk-rules.json（按深度排序，越深的优先级越高）。
    """
    import copy
    rules = copy.deepcopy(_load_default_rules())
    root = Path(repo_dir)
    rule_files = sorted(root.rglob(".sdk-rules.json"), key=lambda p: len(p.relative_to(root).parts))
    for path in rule_files:
        data = _load_one_json(str(path))
        if not data:
            continue
        logger.info("loading rules from %s", path)
        for section in ("role_rules", "layer_rules", "file_filters"):
            if section not in data:
                continue
            target = rules.setdefault(section, {})
            for key, val in data[section].items():
                if isinstance(val, list) and isinstance(target.get(key), list):
                    for v in val:
                        if v not in target[key]:
                            target[key].append(v)
                elif val is not None:
                    target[key] = val
    return rules


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
    modules = discover_modules(repo_dir)
    # rules 全局 merge；annotations 按模块 override
    rules = _load_rules(repo_dir)
    file_filters = rules.get("file_filters", {})
    exclude_dirs = set(file_filters.get("exclude_dirs", []))

    all_chunks: list[dict] = []
    # 收集所有子模块的 pom_dir（标准化路径前缀），用于排除父模块重复解析
    all_pom_dirs = {m.get("pom_dir", "").replace("\\", "/").rstrip("/") + "/" for m in modules}
    for mod in modules:
        pom_dir = mod.get("pom_dir", repo_dir)
        # 每模块独立加载 annotations（override 策略）
        annotations = _load_annotations(pom_dir, repo_dir)
        java_files = list(Path(pom_dir).rglob("*.java"))
        # 过滤 test 目录 + 配置的排除目录 + 子模块目录
        my_pom = pom_dir.replace("\\", "/").rstrip("/") + "/"
        child_dirs = {d for d in all_pom_dirs if d.startswith(my_pom) and d != my_pom}
        src_files = [
            jf for jf in java_files
            if not _path_contains_dir(str(jf), exclude_dirs)
            and not _is_under_child_module(str(jf), child_dirs)
            and "/src/test/" not in str(jf).replace("\\", "/")  # 排除 src/test 目录
        ]
        skipped = len(java_files) - len(src_files)
        if skipped:
            logger.info("module %s: %d java files (%d skipped)", mod.get("artifact_id"), len(java_files), skipped)
        else:
            logger.info("module %s: %d java files", mod.get("artifact_id"), len(java_files))

        for jf in src_files:
            chunks = parse_java_file(str(jf), mod, annotations, rules)
            all_chunks.extend(chunks)

    logger.info("total java chunks: %d", len(all_chunks))
    return all_chunks


def parse_python_repo(repo_dir: str) -> list:
    """解析 Python 仓库：发现 Python 源文件并提取 public 函数/类。

    模块发现：pyproject.toml → setup.py → 目录名。
    只解析 .py 文件，排除 __pycache__ 和 test 目录。
    """
    all_chunks: list = []
    rules = _load_rules(repo_dir)
    file_filters = rules.get("file_filters", {})
    exclude_dirs = set(file_filters.get("exclude_dirs", []))
    exclude_dirs.add("__pycache__")

    # 模块发现：pyproject.toml / setup.py / 目录名
    root = Path(repo_dir)
    module_name = _discover_python_module_name(root)
    mod = {
        "group_id": "", "artifact_id": module_name, "version": "",
        "module_name": module_name, "pom_dir": repo_dir,
    }
    annotations = _load_annotations(repo_dir, repo_dir)

    py_files = list(root.rglob("*.py"))
    # 过滤：__pycache__、test、venv、.venv、build、dist
    src_files = [
        pf for pf in py_files
        if not _path_contains_dir(str(pf), exclude_dirs)
        and "/test/" not in str(pf).replace("\\", "/")
        and "/tests/" not in str(pf).replace("\\", "/")
        and ".venv/" not in str(pf).replace("\\", "/")
        and ".tox/" not in str(pf).replace("\\", "/")
    ]
    skipped = len(py_files) - len(src_files)
    logger.info("module %s: %d python files (%d skipped)", module_name, len(py_files), skipped)

    for pf in src_files:
        chunks = _parse_python_file(str(pf), mod, annotations, rules)
        all_chunks.extend(chunks)

    logger.info("total python chunks: %d", len(all_chunks))
    return all_chunks


def _discover_python_module_name(root: Path) -> str:
    """从 pyproject.toml / setup.py 提取 Python 项目名，无则用目录名。"""
    for cfg in [root / "pyproject.toml", root / "setup.cfg"]:
        if cfg.exists():
            try:
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("name "):
                        name = stripped.split("=", 1)[-1].strip().strip('"').strip("'")
                        if name:
                            return name
            except Exception:
                pass
    setup_py = root / "setup.py"
    if setup_py.exists():
        try:
            text = setup_py.read_text(encoding="utf-8")
            import re
            m = re.search(r'''name\s*=\s*['"]([^'"]+)['"]''', text)
            if m:
                return m.group(1)
        except Exception:
            pass
    return root.resolve().name


def _parse_python_file(filepath: str, sdk_meta: dict, annotations: dict | None = None, rules: dict | None = None) -> list:
    """解析单个 .py 文件，提取所有函数和类方法为 PipelineChunk。"""
    from .models import ChunkMeta, PipelineChunk

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except Exception:
        return []

    try:
        from .adapters import parse_source
        tree = parse_source("python", source)
    except Exception:
        logger.warning("python parse failed: %s", filepath)
        return []

    from .adapters.base import _ClassDecl, _MethodDecl

    chunks: list = []
    sdk_id = sdk_meta.get("artifact_id", os.path.basename(os.path.dirname(filepath)))
    class_stack: list[str] = [os.path.splitext(os.path.basename(filepath))[0]]
    module_path = _python_module_path(filepath, sdk_meta.get("pom_dir", ""))

    for path, node in tree:
        if isinstance(node, _ClassDecl):
            class_stack.append(node.name)
        elif isinstance(node, _MethodDecl):
            fqn = ".".join(class_stack[1:] + [node.name])
            rt = node.return_type.name if node.return_type else ""

            params_str = ", ".join(f"{p.type.name} {p.name}" for p in node.parameters if p.name)
            content = f"{fqn}({params_str})"
            if rt:
                content += f" → {rt}"

            chunk_id = _make_id(f"{sdk_id}:{fqn}")
            meta = ChunkMeta(
                knowledge_source="sdk_code", sdk=sdk_id, class_name=module_path,
                method=node.name, return_type=rt, role="public_api", layer="mid",
            )
            chunks.append(PipelineChunk(
                id=chunk_id, type="api", content=content,
                title=".".join(class_stack[1:-1] + [node.name]) if len(class_stack) > 2 else node.name,
                module=sdk_id, source_path=filepath, meta=meta,
            ))

    return chunks


def _python_module_path(filepath: str, repo_dir: str) -> str:
    """从 .py 文件路径推导 Python 模块路径（如 com.company.service）。"""
    rel = os.path.relpath(filepath, repo_dir) if repo_dir else filepath
    rel = rel.replace("\\", "/")
    for prefix in ("src/main/python/", "src/", ""):
        if prefix and prefix in rel:
            rel = rel.split(prefix, 1)[-1]
            break
    parts = rel.replace("/", ".").replace(".py", "").split(".")
    meaningful = [p for p in parts if p and not p.startswith("_")]
    return ".".join(meaningful) or os.path.splitext(os.path.basename(filepath))[0]


def parse_typescript_repo(repo_dir: str) -> list:
    """解析 TypeScript 仓库：发现 .ts/.tsx 文件并提取 public 函数/类。

    模块发现：package.json → tsconfig.json → 目录名。
    """
    all_chunks: list = []
    rules = _load_rules(repo_dir)
    file_filters = rules.get("file_filters", {})
    exclude_dirs = set(file_filters.get("exclude_dirs", []))
    exclude_dirs.add("__tests__")

    root = Path(repo_dir)
    module_name = _discover_ts_module_name(root)
    mod = {
        "group_id": "", "artifact_id": module_name, "version": "",
        "module_name": module_name, "pom_dir": repo_dir,
    }
    annotations = _load_annotations(repo_dir, repo_dir)

    ts_files = list(root.rglob("*.ts")) + list(root.rglob("*.tsx"))
    src_files = [
        tf for tf in ts_files
        if not _path_contains_dir(str(tf), exclude_dirs)
        and "__tests__" not in str(tf).replace("\\", "/")
        and ".spec.ts" not in str(tf)
        and ".test.ts" not in str(tf)
        and "node_modules/" not in str(tf).replace("\\", "/")
    ]
    skipped = len(ts_files) - len(src_files)
    logger.info("module %s: %d ts files (%d skipped)", module_name, len(ts_files), skipped)

    for tf in src_files:
        chunks = _parse_typescript_file(str(tf), mod, annotations, rules)
        all_chunks.extend(chunks)

    logger.info("total typescript chunks: %d", len(all_chunks))
    return all_chunks


def _discover_ts_module_name(root: Path) -> str:
    """从 package.json 提取 TypeScript 项目名。"""
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            import json
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            name = data.get("name", "")
            if name:
                return name
        except Exception:
            pass
    return root.resolve().name


def _parse_typescript_file(filepath: str, sdk_meta: dict, annotations: dict | None = None, rules: dict | None = None) -> list:
    """解析单个 .ts 文件，提取所有 public 函数和类方法为 PipelineChunk。"""
    from .models import ChunkMeta, PipelineChunk

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except Exception:
        return []

    try:
        from .adapters import parse_source
        tree = parse_source("typescript", source)
    except Exception:
        logger.warning("typescript parse failed: %s", filepath)
        return []

    from .adapters.base import _ClassDecl, _ConstructorDecl, _MethodDecl

    chunks: list = []
    sdk_id = sdk_meta.get("artifact_id", os.path.basename(os.path.dirname(filepath)))
    class_stack: list[str] = [os.path.splitext(os.path.basename(filepath))[0]]
    seen_keys: set[tuple] = set()  # 去重

    for path, node in tree:
        if isinstance(node, _ClassDecl):
            class_stack.append(node.name)
        elif isinstance(node, _MethodDecl) or isinstance(node, _ConstructorDecl):
            mods = node.modifiers if hasattr(node, "modifiers") else []

            # TS: constructor 默认 public；explicit private/protected 才跳过
            if isinstance(node, _ConstructorDecl) and ("private" in mods or "protected" in mods):
                continue
            if isinstance(node, _MethodDecl) and "private" in mods:
                continue

            cls = ".".join(class_stack[1:])
            key = (cls, node.name)
            if key in seen_keys or not node.name:
                continue
            seen_keys.add(key)

            fqn = ".".join(class_stack[1:] + [node.name])
            rt = node.return_type.name if node.return_type else ""
            params_str = ", ".join(f"{p.type.name} {p.name}" for p in node.parameters if p.name)
            content = f"{fqn}({params_str})"
            if rt:
                content += f" → {rt}"

            chunk_id = _make_id(f"{sdk_id}:{fqn}")
            meta = ChunkMeta(
                knowledge_source="sdk_code", sdk=sdk_id, class_name=cls,
                method=node.name, return_type=rt, role="public_api", layer="mid",
            )
            chunks.append(PipelineChunk(
                id=chunk_id, type="api", content=content,
                title=".".join(class_stack[1:-1] + [node.name]) if len(class_stack) > 2 else node.name,
                module=sdk_id, source_path=filepath, meta=meta,
            ))

    return chunks


def _is_under_child_module(file_path: str, pom_dirs: set[str]) -> bool:
    """检查文件是否在子模块的 pom_dir 下（前缀匹配）。"""
    normalized = file_path.replace("\\", "/")
    for d in pom_dirs:
        if normalized.startswith(d):
            return True
    return False


def _path_contains_dir(path_str: str, exclude_dirs: set) -> bool:
    """检查文件路径中是否包含需要排除的目录名。"""
    parts = set(path_str.replace("\\", "/").split("/"))
    return bool(parts & exclude_dirs)
