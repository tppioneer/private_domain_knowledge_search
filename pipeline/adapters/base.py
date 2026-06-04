"""语言无关的 AST 节点类型 —— 所有 language adapter 共享。

每个 adapter 将 tree-sitter CST 包装为以下类型，
使 code_parser.py 可以无差别处理 Java/Python/TypeScript。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── 节点类型 ──


class _Position:
    """源码位置。"""
    def __init__(self, ts_node):
        sp = ts_node.start_point
        self.line = sp[0] + 1


class _TypeRef:
    """类型引用。"""
    def __init__(self, name: str):
        self.name = name
        self.arguments: list = []


class _Param:
    """形参。"""
    def __init__(self, type_name: str, param_name: str):
        self.type = _TypeRef(type_name)
        self.name = param_name


class _MethodDecl:
    """方法/函数声明。"""
    def __init__(self, ts_node):
        self._ts = ts_node
        self.modifiers: list[str] = []
        self.name = ""
        self.return_type: _TypeRef | None = None
        self.parameters: list[_Param] = []
        self.position = _Position(ts_node)


class _ConstructorDecl:
    """构造函数声明。"""
    def __init__(self, ts_node):
        self._ts = ts_node
        self.modifiers: list[str] = []
        self.name = ""
        self.parameters: list[_Param] = []
        self.position = _Position(ts_node)


class _ClassDecl:
    """类声明。"""
    def __init__(self, name: str):
        self.name = name


class _InterfaceDecl:
    """接口声明。"""
    def __init__(self, name: str):
        self.name = name


class _MethodInvocation:
    """方法调用。"""
    def __init__(self, method_name: str):
        self.member = method_name


class _PackageDecl:
    """包/模块声明。"""
    def __init__(self, name: str):
        self.name = name


# ── 树遍历 ──


class TreeAdapter:
    """包装 tree-sitter 树，提供 for path, node in tree 迭代。"""

    def __init__(self, ts_tree, wrap_func):
        self._ts_tree = ts_tree
        self._wrap = wrap_func
        self.package = None

    def __iter__(self):
        root = self._ts_tree.root_node
        stack: list[tuple, list] = [(root, [])]
        while stack:
            ts_node, path = stack.pop()
            wrapped = self._wrap(ts_node)
            if wrapped is not None:
                yield path, wrapped
            for child in reversed(ts_node.children):
                stack.append((child, path + [wrapped] if wrapped else path))


# ── 工具函数 ──


def _inner_text(ts_node) -> str:
    return ts_node.text.decode("utf-8", errors="replace") if ts_node.text else ""


def _child_text(ts_node, child_type: str) -> str:
    for child in ts_node.children:
        if child.type == child_type:
            return _inner_text(child)
    return ""


def _field_text(ts_node, field_name: str) -> str:
    """获取指定 field 的文本。tree-sitter 中 name: (identifier) 等。"""
    for child in ts_node.children:
        if child.type == field_name:
            return _inner_text(child)
    return ""
