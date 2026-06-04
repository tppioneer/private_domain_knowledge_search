"""Java tree-sitter adapter —— 将 CST 包装为 base.py 的通用节点类型。"""

from __future__ import annotations

import logging

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser

from .base import (
    TreeAdapter, _ClassDecl, _ConstructorDecl, _InterfaceDecl,
    _MethodDecl, _MethodInvocation, _PackageDecl, _Param, _TypeRef,
    _child_text, _inner_text,
)

logger = logging.getLogger(__name__)

_JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_JAVA_LANGUAGE)

_MODIFIER_TYPES = {"public", "private", "protected", "static",
                   "final", "abstract", "synchronized", "native", "strictfp", "default"}


def _extract_modifiers(ts_node) -> list[str]:
    mods: list[str] = []
    for child in ts_node.children:
        if child.type == "modifiers":
            for c in child.children:
                if c.type in _MODIFIER_TYPES:
                    mods.append(_inner_text(c))
    return mods


def _type_name(ts_node) -> str:
    parts: list[str] = []
    _collect_type_name(ts_node, parts)
    return "".join(parts)


def _collect_type_name(ts_node, parts: list[str]) -> None:
    if ts_node.type == "generic_type":
        for child in ts_node.children:
            if child.type == "type_identifier":
                parts.append(_inner_text(child))
            elif child.type == "type_arguments":
                parts.append("<")
                args = [_inner_text(c) for c in child.children if c.type == "type_identifier"]
                parts.append(", ".join(args))
                parts.append(">")
    elif ts_node.type in ("type_identifier", "void_type",
                          "integral_type", "floating_point_type", "boolean_type"):
        parts.append(_inner_text(ts_node))
    elif ts_node.type == "array_type":
        _collect_type_name(ts_node.children[0], parts)
        parts.append("[]")
    else:
        for child in ts_node.children:
            _collect_type_name(child, parts)
            if parts:
                return


def _fill_method(method: _MethodDecl, ts_node) -> None:
    for child in ts_node.children:
        ctype = child.type
        if ctype == "identifier":
            method.name = _inner_text(child)
        elif ctype in ("void_type", "integral_type", "floating_point_type",
                       "boolean_type", "type_identifier", "generic_type", "array_type"):
            method.return_type = _TypeRef(_type_name(child))
        elif ctype == "formal_parameters":
            _fill_params(method.parameters, child)
        elif not method.return_type:
            for sub in child.children:
                if sub.type in ("void_type", "integral_type", "floating_point_type",
                               "boolean_type", "type_identifier", "generic_type", "array_type"):
                    method.return_type = _TypeRef(_type_name(sub))
                    break
    if not method.return_type:
        for child in ts_node.children:
            _find_return_type(child, method)


def _find_return_type(ts_node, method: _MethodDecl) -> None:
    if method.return_type:
        return
    for child in ts_node.children:
        if child.type in ("void_type", "integral_type", "floating_point_type",
                         "boolean_type", "type_identifier", "generic_type", "array_type"):
            method.return_type = _TypeRef(_type_name(child))
            return
        _find_return_type(child, method)


def _fill_constructor(ctor, ts_node) -> None:
    for child in ts_node.children:
        if child.type == "identifier":
            ctor.name = _inner_text(child)
        elif child.type == "formal_parameters":
            _fill_params(ctor.parameters, child)


def _fill_params(params_list: list[_Param], params_node) -> None:
    for child in params_node.children:
        if child.type == "formal_parameter":
            type_name = ""
            param_name = ""
            for sub in child.children:
                st = sub.type
                if st in ("type_identifier", "void_type", "integral_type",
                         "floating_point_type", "boolean_type", "generic_type", "array_type"):
                    type_name = _type_name(sub)
                elif st == "identifier":
                    param_name = _inner_text(sub)
                elif st == "spread_parameter":
                    for ss in sub.children:
                        if ss.type in ("type_identifier", "integral_type",
                                      "floating_point_type", "boolean_type", "generic_type"):
                            type_name = _type_name(ss) + "..."
                        elif ss.type == "identifier":
                            param_name = _inner_text(ss)
            if type_name:
                params_list.append(_Param(type_name, param_name))


def _wrap_node(ts_node):
    node_type = ts_node.type
    if node_type == "class_declaration":
        name = _child_text(ts_node, "identifier") or _inner_text(ts_node)
        return _ClassDecl(name)
    elif node_type == "interface_declaration":
        name = _child_text(ts_node, "identifier") or _inner_text(ts_node)
        return _InterfaceDecl(name)
    elif node_type == "method_declaration":
        m = _MethodDecl(ts_node)
        m.modifiers = _extract_modifiers(ts_node)
        _fill_method(m, ts_node)
        return m
    elif node_type == "constructor_declaration":
        c = _ConstructorDecl(ts_node)
        c.modifiers = _extract_modifiers(ts_node)
        _fill_constructor(c, ts_node)
        return c
    elif node_type == "method_invocation":
        name = _extract_invocation_name(ts_node)
        if name:
            return _MethodInvocation(name)
    elif node_type == "package_declaration":
        for child in ts_node.children:
            if child.type == "scoped_identifier":
                return _PackageDecl(_inner_text(child))
        return _PackageDecl(_child_text(ts_node, "identifier") or "")
    return None


def _extract_invocation_name(ts_node) -> str:
    for child in ts_node.children:
        if child.type == "identifier":
            return _inner_text(child)
        elif child.type == "field_access":
            for sub in child.children:
                if sub.type == "identifier":
                    return _inner_text(sub)
    return ""


def _walk_method_invocations(ts_node, calls: list[str]) -> None:
    if ts_node.type == "method_invocation":
        name = _extract_invocation_name(ts_node)
        if name:
            calls.append(name)
    for child in ts_node.children:
        _walk_method_invocations(child, calls)


def _extract_package_ts(root_node) -> _PackageDecl | None:
    for child in root_node.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type == "scoped_identifier":
                    return _PackageDecl(_inner_text(sub))
            pkg_name = _child_text(child, "identifier")
            if pkg_name:
                return _PackageDecl(pkg_name)
    return None


# ── 公开 API ──


def parse(source: str) -> TreeAdapter:
    ts_tree = _PARSER.parse(source.encode("utf-8"))
    adapter = TreeAdapter(ts_tree, _wrap_node)
    adapter.package = _extract_package_ts(ts_tree.root_node)
    return adapter


def extract_calls(method_wrapper: _MethodDecl) -> list[str]:
    ts_node = method_wrapper._ts
    calls: list[str] = []
    _walk_method_invocations(ts_node, calls)
    return calls
