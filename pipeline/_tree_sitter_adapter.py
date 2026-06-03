"""tree-sitter → javalang 兼容适配层。

将 tree-sitter CST 包装为 javalang 风格的 AST 节点对象，
使 code_parser.py 可以无感切换到 tree-sitter 解析后端。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── tree-sitter 初始化 ──
import tree_sitter_java as tsjava
from tree_sitter import Language, Node as TSNode, Parser

_JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_JAVA_LANGUAGE)

# ── javalang 兼容节点类型 ──


class _Position:
    """模拟 javalang.tokenizer.Position。"""
    def __init__(self, ts_node: TSNode):
        sp = ts_node.start_point
        self.line = sp[0] + 1  # tree-sitter 0-based → 1-based


class _TypeRef:
    """模拟 javalang.tree.ReferenceType / BasicType。"""
    def __init__(self, name: str):
        self.name = name
        self.arguments: list = []


class _Param:
    """模拟 javalang.tree.FormalParameter。"""
    def __init__(self, type_name: str, param_name: str):
        self.type = _TypeRef(type_name)
        self.name = param_name


class _MethodDecl:
    """模拟 javalang.tree.MethodDeclaration。"""
    def __init__(self, ts_node: TSNode):
        self._ts = ts_node
        self.modifiers = _extract_modifiers(ts_node)
        self.name = ""
        self.return_type: _TypeRef | None = None
        self.parameters: list[_Param] = []
        self.position = _Position(ts_node)
        # 从子节点提取
        _fill_method(self, ts_node)


class _ConstructorDecl:
    """模拟 javalang.tree.ConstructorDeclaration。"""
    def __init__(self, ts_node: TSNode):
        self._ts = ts_node
        self.modifiers = _extract_modifiers(ts_node)
        self.name = ""
        self.parameters: list[_Param] = []
        self.position = _Position(ts_node)
        _fill_constructor(self, ts_node)


class _ClassDecl:
    """模拟 javalang.tree.ClassDeclaration。"""
    def __init__(self, ts_node: TSNode):
        self.name = _child_text(ts_node, "identifier") or ts_node.text.decode()[:50]
        self._ts = ts_node


class _InterfaceDecl:
    """模拟 javalang.tree.InterfaceDeclaration。"""
    def __init__(self, ts_node: TSNode):
        self.name = _child_text(ts_node, "identifier") or ts_node.text.decode()[:50]


class _MethodInvocation:
    """模拟 javalang.tree.MethodInvocation。"""
    def __init__(self, method_name: str):
        self.member = method_name


class _PackageDecl:
    """模拟 javalang.tree.PackageDeclaration。"""
    def __init__(self, name: str):
        self.name = name


# ── 树遍历适配 ──


class _TreeAdapter:
    """包装 tree-sitter 树，提供类似 javalang 的 for path, node in tree 迭代。"""

    def __init__(self, ts_tree):
        self._ts_tree = ts_tree
        self.package = None  # 由 parse 后设置

    def __iter__(self):
        """深度优先遍历，yield (path, wrapped_node)。"""
        root = self._ts_tree.root_node
        stack: list[tuple[TSNode, list]] = [(root, [])]

        while stack:
            ts_node, path = stack.pop()

            # 跳过叶子和无意义的中间节点
            wrapped = _wrap_node(ts_node)
            if wrapped is not None:
                yield path, wrapped

            # 深度优先：先放后面的子节点（因为栈是 LIFO）
            for child in reversed(ts_node.children):
                stack.append((child, path + [wrapped] if wrapped else path))


# ═══════════════════════════════════════════════
# 内部辅助函数
# ═══════════════════════════════════════════════

_MODIFIER_TYPES = {"public", "private", "protected", "static",
                   "final", "abstract", "synchronized", "native", "strictfp", "default"}


def _extract_modifiers(ts_node: TSNode) -> list[str]:
    """从 tree-sitter 节点提取修饰符列表。"""
    mods: list[str] = []
    for child in ts_node.children:
        if child.type == "modifiers":
            for c in child.children:
                if c.type in _MODIFIER_TYPES:
                    mods.append(_inner_text(c))
    return mods


def _child_text(ts_node: TSNode, child_type: str) -> str:
    """获取指定类型的直接子节点文本。"""
    for child in ts_node.children:
        if child.type == child_type:
            return _inner_text(child)
    return ""


def _inner_text(ts_node: TSNode) -> str:
    """获取节点的内部文本（去叶子空格）。"""
    return ts_node.text.decode("utf-8", errors="replace") if ts_node.text else ""


def _type_name(ts_node: TSNode) -> str:
    """从类型节点提取类型名。处理泛型。"""
    # 递归解析类型引用
    parts: list[str] = []
    _collect_type_name(ts_node, parts)
    return "".join(parts)


def _collect_type_name(ts_node: TSNode, parts: list[str]) -> None:
    """递归收集类型名（含泛型参数）。"""
    if ts_node.type == "generic_type":
        # List<FileService> → "List"
        for child in ts_node.children:
            if child.type == "type_identifier":
                parts.append(_inner_text(child))
            elif child.type == "type_arguments":
                parts.append("<")
                args: list[str] = []
                for c in child.children:
                    if c.type == "type_identifier":
                        args.append(_inner_text(c))
                parts.append(", ".join(args))
                parts.append(">")
    elif ts_node.type in ("type_identifier", "void_type",
                          "integral_type", "floating_point_type", "boolean_type"):
        parts.append(_inner_text(ts_node))
    elif ts_node.type == "array_type":
        _collect_type_name(ts_node.children[0], parts)
        parts.append("[]")
    else:
        # 递归子节点
        for child in ts_node.children:
            _collect_type_name(child, parts)
            if parts:
                return


def _fill_method(method: _MethodDecl, ts_node: TSNode) -> None:
    """填充方法声明的详细信息。"""
    for child in ts_node.children:
        ctype = child.type
        if ctype == "identifier":
            method.name = _inner_text(child)
        elif ctype in ("void_type", "integral_type", "floating_point_type",
                       "boolean_type", "type_identifier", "generic_type",
                       "array_type"):
            method.return_type = _TypeRef(_type_name(child))
        elif ctype == "formal_parameters":
            _fill_params(method.parameters, child)
        # 如果 return_type 还没找到，继续在子节点中找
        elif not method.return_type:
            for sub in child.children:
                if sub.type in ("void_type", "integral_type", "floating_point_type",
                               "boolean_type", "type_identifier", "generic_type",
                               "array_type"):
                    method.return_type = _TypeRef(_type_name(sub))
                    break

    # 如果 return_type 仍未找到，回退搜索（有些嵌套场景）
    if not method.return_type:
        for child in ts_node.children:
            _find_return_type(child, method)


def _find_return_type(ts_node: TSNode, method: _MethodDecl) -> None:
    """递归搜索返回类型。"""
    if method.return_type:
        return
    for child in ts_node.children:
        if child.type in ("void_type", "integral_type", "floating_point_type",
                         "boolean_type", "type_identifier", "generic_type",
                         "array_type"):
            method.return_type = _TypeRef(_type_name(child))
            return
        _find_return_type(child, method)


def _fill_constructor(ctor: _ConstructorDecl, ts_node: TSNode) -> None:
    """填充构造函数的详细信息。"""
    for child in ts_node.children:
        ctype = child.type
        if ctype == "identifier":
            ctor.name = _inner_text(child)
        elif ctype == "formal_parameters":
            _fill_params(ctor.parameters, child)


def _fill_params(params_list: list[_Param], params_node: TSNode) -> None:
    """填充形参列表。"""
    for child in params_node.children:
        if child.type == "formal_parameter":
            type_name = ""
            param_name = ""
            for sub in child.children:
                st = sub.type
                if st in ("type_identifier", "void_type", "integral_type",
                         "floating_point_type", "boolean_type", "generic_type",
                         "array_type"):
                    type_name = _type_name(sub)
                elif st == "identifier":
                    param_name = _inner_text(sub)
                elif st in ("spread_parameter",):
                    # varargs: String... args
                    for ss in sub.children:
                        if ss.type in ("type_identifier", "integral_type",
                                      "floating_point_type", "boolean_type", "generic_type"):
                            type_name = _type_name(ss) + "..."
                        elif ss.type == "identifier":
                            param_name = _inner_text(ss)
            if type_name:
                params_list.append(_Param(type_name, param_name))


def _wrap_node(ts_node: TSNode):
    """将 tree-sitter 节点包装为 javalang 兼容对象。仅包装感兴趣的节点类型。"""
    node_type = ts_node.type

    if node_type == "class_declaration":
        return _ClassDecl(ts_node)
    elif node_type == "interface_declaration":
        return _InterfaceDecl(ts_node)
    elif node_type == "method_declaration":
        return _MethodDecl(ts_node)
    elif node_type == "constructor_declaration":
        return _ConstructorDecl(ts_node)
    elif node_type == "method_invocation":
        name = ""
        for child in ts_node.children:
            ctype = child.type
            if ctype == "identifier":
                name = _inner_text(child)
            elif ctype == "field_access":
                # obj.method() → "method"
                for sub in child.children:
                    if sub.type == "identifier":
                        name = _inner_text(sub)
        if name:
            return _MethodInvocation(name)
    elif node_type == "package_declaration":
        # 提取包名
        for child in ts_node.children:
            if child.type == "scoped_identifier":
                return _PackageDecl(_inner_text(child))
        return _PackageDecl(_child_text(ts_node, "identifier") or "")

    return None


# ═══════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════


def parse(source: str):
    """解析 Java 源码，返回可迭代的 tree-sitter 兼容树。

    用法: for path, node in tree: ...
    """
    ts_tree = _PARSER.parse(source.encode("utf-8"))
    adapter = _TreeAdapter(ts_tree)
    # 提取包名
    adapter.package = _extract_package_ts(ts_tree.root_node)
    return adapter


def extract_calls(method_wrapper: _MethodDecl) -> list[str]:
    """从方法体内提取调用的方法名列表（替代 javalang 的 _extract_calls）。"""
    ts_node = method_wrapper._ts
    calls: list[str] = []
    _walk_method_invocations(ts_node, calls)
    return calls


def _walk_method_invocations(ts_node: TSNode, calls: list[str]) -> None:
    """递归遍历子树，收集 method_invocation 的 member 名。"""
    if ts_node.type == "method_invocation":
        # 找到方法调用名
        name = _extract_invocation_name(ts_node)
        if name:
            calls.append(name)
    for child in ts_node.children:
        _walk_method_invocations(child, calls)


def _extract_invocation_name(ts_node: TSNode) -> str:
    """从 method_invocation 节点提取方法名。"""
    for child in ts_node.children:
        if child.type == "identifier":
            return _inner_text(child)
        elif child.type == "field_access":
            # obj.method() → "method"
            for sub in child.children:
                if sub.type == "identifier":
                    return _inner_text(sub)
    return ""


def _extract_package_ts(root_node: TSNode) -> _PackageDecl | None:
    """从 tree-sitter 根节点提取包声明。"""
    for child in root_node.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    return _PackageDecl(_inner_text(sub))
            pkg_name = _child_text(child, "identifier")
            if pkg_name:
                return _PackageDecl(pkg_name)
    return None
