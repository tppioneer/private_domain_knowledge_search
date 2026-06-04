"""Python tree-sitter adapter。"""

from __future__ import annotations

import tree_sitter_python as tspy
from tree_sitter import Language, Parser

from .base import (
    TreeAdapter, _ClassDecl, _ConstructorDecl, _MethodDecl,
    _MethodInvocation, _Param, _TypeRef,
    _child_text, _inner_text,
)

_PY_LANGUAGE = Language(tspy.language())
_PARSER = Parser(_PY_LANGUAGE)

_PRIVATE_PREFIX = "_"
_DECORATOR_STATIC = {"staticmethod", "classmethod"}
_RETURN_TYPES = {"type", "generic_type", "none"}


def _extract_modifiers(ts_node, decorator: dict | None = None) -> list[str]:
    mods: list[str] = []
    if decorator:
        name = decorator.get("name", "")
        if name in _DECORATOR_STATIC:
            mods.append("static")
        elif name.startswith(_PRIVATE_PREFIX):
            mods.append("private")
    else:
        # No decorator = instance method (public by Python convention)
        pass
    return mods


def _extract_return_type(ts_node) -> _TypeRef | None:
    """Python: def foo(x) -> ReturnType:"""
    for child in ts_node.children:
        if child.type == "return_type":
            for sub in child.children:
                if sub.type in _RETURN_TYPES:
                    return _TypeRef(_inner_text(sub))
    return None


def _extract_decorator(ts_node) -> dict | None:
    """从 decorated_definition 提取装饰器信息。"""
    for child in ts_node.children:
        if child.type == "decorator":
            name = ""
            for sub in child.children:
                if sub.type == "identifier":
                    name = _inner_text(sub)
                elif sub.type == "attribute":
                    for ss in sub.children:
                        if ss.type == "identifier":
                            name = _inner_text(ss)
            if name:
                return {"name": name}
    return None


def _fill_method(method: _MethodDecl, ts_node) -> None:
    for child in ts_node.children:
        ctype = child.type
        if ctype == "identifier":
            method.name = _inner_text(child)
        elif ctype == "parameters":
            _fill_params(method.parameters, child)
    method.return_type = _extract_return_type(ts_node)


def _fill_params(params_list: list[_Param], params_node) -> None:
    for child in params_node.children:
        if child.type in ("identifier", "typed_parameter", "default_parameter",
                          "list_splat_pattern", "dictionary_splat_pattern"):
            type_name = ""
            param_name = ""
            for sub in child.children:
                st = sub.type
                if st == "identifier":
                    param_name = _inner_text(sub)
                elif st in _RETURN_TYPES:
                    type_name = _inner_text(sub)
            if param_name:
                params_list.append(_Param(type_name, param_name))


def _collect_class_decorators(ts_node) -> list[str]:
    """Python: @dataclass, @attr.s, etc."""
    mods: list[str] = []
    for child in ts_node.children:
        if child.type == "decorator":
            for sub in child.children:
                if sub.type == "identifier":
                    mods.append(_inner_text(sub))
    return mods


def _make_wrap():
    """返回 (wrap_func, decorated_ids) —— decorated_ids 跟踪已处理的装饰节点。"""
    decorated_ids: set[int] = set()

    def _wrap(ts_node):
        nonlocal decorated_ids
        node_type = ts_node.type

        if node_type == "decorated_definition":
            decorator = _extract_decorator(ts_node)
            inner = None
            for child in ts_node.children:
                if child.type in ("function_definition", "class_definition"):
                    inner = child
                    break
            if inner is None:
                return None
            decorated_ids.add(id(inner))
            if inner.type == "function_definition":
                m = _MethodDecl(inner)
                m.modifiers = _extract_modifiers(inner, decorator)
                _fill_method(m, inner)
                return m
            elif inner.type == "class_definition":
                name = _child_text(inner, "identifier") or _inner_text(inner)
                return _ClassDecl(name)

        elif node_type == "function_definition":
            if id(ts_node) in decorated_ids:
                return None  # 已被 decorated_definition 处理
            m = _MethodDecl(ts_node)
            m.modifiers = _extract_modifiers(ts_node)
            _fill_method(m, ts_node)
            return m

        elif node_type == "class_definition":
            name = _child_text(ts_node, "identifier") or _inner_text(ts_node)
            return _ClassDecl(name)

        elif node_type == "call":
            for child in ts_node.children:
                if child.type == "identifier":
                    return _MethodInvocation(_inner_text(child))
                elif child.type == "attribute":
                    for sub in child.children:
                        if sub.type == "identifier":
                            return _MethodInvocation(_inner_text(sub))

        return None

    return _wrap


def _walk_calls(ts_node, calls: list[str]) -> None:
    if ts_node.type == "call":
        for child in ts_node.children:
            if child.type == "identifier":
                calls.append(_inner_text(child))
            elif child.type == "attribute":
                for sub in child.children:
                    if sub.type == "identifier":
                        calls.append(_inner_text(sub))
    for child in ts_node.children:
        _walk_calls(child, calls)


# ── 公开 API ──


def parse(source: str) -> TreeAdapter:
    ts_tree = _PARSER.parse(source.encode("utf-8"))
    wrap = _make_wrap()
    return TreeAdapter(ts_tree, wrap)


def extract_calls(method_wrapper: _MethodDecl) -> list[str]:
    ts_node = method_wrapper._ts
    calls: list[str] = []
    _walk_calls(ts_node, calls)
    return calls
