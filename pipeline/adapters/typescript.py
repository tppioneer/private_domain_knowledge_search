"""TypeScript tree-sitter adapter。"""

from __future__ import annotations

import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser

from .base import (
    TreeAdapter, _ClassDecl, _ConstructorDecl, _InterfaceDecl,
    _MethodDecl, _MethodInvocation, _Param, _TypeRef,
    _child_text, _inner_text,
)

_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())
_PARSER = Parser(_TS_LANGUAGE)
_PARSER_TSX = Parser(_TSX_LANGUAGE)

_MODIFIER_TYPES = {"public", "private", "protected", "static",
                   "abstract", "readonly", "async", "export", "declare", "default"}


def _extract_modifiers(ts_node) -> list[str]:
    mods: list[str] = []
    for child in ts_node.children:
        if child.type in _MODIFIER_TYPES:
            mods.append(child.type)
        elif child.type == "accessibility_modifier":
            mods.append(_inner_text(child))
    return mods


def _type_annotation_text(ts_node) -> str:
    """提取 TypeScript 类型注解文本。"""
    parts: list[str] = []
    _collect_type_text(ts_node, parts)
    return "".join(parts)


def _collect_type_text(ts_node, parts: list[str]) -> None:
    if ts_node.type == "generic_type":
        for child in ts_node.children:
            if child.type == "type_identifier":
                parts.append(_inner_text(child))
            elif child.type == "type_arguments":
                parts.append("<")
                args = [_inner_text(c) for c in child.children if c.type == "type_identifier"]
                parts.append(", ".join(args))
                parts.append(">")
    elif ts_node.type in ("type_identifier", "predefined_type", "number", "string",
                          "boolean", "void", "any", "unknown", "never", "undefined", "null"):
        parts.append(_inner_text(ts_node))
    elif ts_node.type == "array_type":
        _collect_type_text(ts_node.children[0], parts)
        parts.append("[]")
    else:
        for child in ts_node.children:
            _collect_type_text(child, parts)
            if parts:
                return


def _extract_return_type(ts_node) -> _TypeRef | None:
    """TypeScript: function foo(): ReturnType { ... } / (x): ReturnType => ..."""
    for child in ts_node.children:
        if child.type == "return_type":
            for sub in child.children:
                if sub.type in ("type_annotation",):
                    for s2 in sub.children:
                        if s2.type not in (":",):
                            return _TypeRef(_type_annotation_text(s2))
            break
    return None


def _fill_method(method: _MethodDecl, ts_node) -> None:
    for child in ts_node.children:
        ctype = child.type
        if ctype == "property_identifier":
            method.name = _inner_text(child)
        elif ctype in ("formal_parameters", "call_signature"):
            _fill_params(method.parameters, child)
    method.return_type = _extract_return_type(ts_node)
    # Fallback: extract name from identifier
    if not method.name:
        method.name = _child_text(ts_node, "property_identifier") or _child_text(ts_node, "identifier") or ""


def _fill_constructor(ctor, ts_node) -> None:
    for child in ts_node.children:
        if child.type in ("formal_parameters", "call_signature"):
            _fill_params(ctor.parameters, child)


def _fill_params(params_list: list[_Param], params_node) -> None:
    for child in params_node.children:
        if child.type in ("required_parameter", "optional_parameter"):
            type_name = ""
            param_name = ""
            for sub in child.children:
                st = sub.type
                if st == "property_identifier" or st == "identifier":
                    param_name = _inner_text(sub)
                elif st == "type_annotation":
                    for s2 in sub.children:
                        if s2.type not in (":",):
                            type_name = _type_annotation_text(s2)
                            break
            if param_name:
                params_list.append(_Param(type_name, param_name))


def _wrap_node(ts_node):
    node_type = ts_node.type

    # Skip ambient/declare blocks — only process concrete declarations inside
    if node_type == "ambient_declaration":
        return None

    if node_type == "class_declaration":
        name = _child_text(ts_node, "type_identifier") or _inner_text(ts_node)
        return _ClassDecl(name)

    elif node_type == "interface_declaration":
        name = _child_text(ts_node, "type_identifier") or _inner_text(ts_node)
        return _InterfaceDecl(name)

    elif node_type == "method_definition":
        m = _MethodDecl(ts_node)
        m.modifiers = _extract_modifiers(ts_node)
        _fill_method(m, ts_node)
        return m

    elif node_type == "function_declaration":
        m = _MethodDecl(ts_node)
        m.modifiers = _extract_modifiers(ts_node)
        _fill_method(m, ts_node)
        return m

    elif node_type == "constructor_declaration" or node_type == "public_field_definition":
        c = _ConstructorDecl(ts_node)
        c.modifiers = _extract_modifiers(ts_node)
        _fill_constructor(c, ts_node)
        return c

    elif node_type == "call_expression":
        for child in ts_node.children:
            if child.type == "identifier":
                return _MethodInvocation(_inner_text(child))
            elif child.type == "member_expression":
                for sub in child.children:
                    if sub.type == "property_identifier":
                        return _MethodInvocation(_inner_text(sub))

    return None


def _walk_calls(ts_node, calls: list[str]) -> None:
    if ts_node.type == "call_expression":
        for child in ts_node.children:
            if child.type == "identifier":
                calls.append(_inner_text(child))
            elif child.type == "member_expression":
                for sub in child.children:
                    if sub.type == "property_identifier":
                        calls.append(_inner_text(sub))
    for child in ts_node.children:
        _walk_calls(child, calls)


# ── 公开 API ──


def parse(source: str, tsx: bool = False) -> TreeAdapter:
    parser = _PARSER_TSX if tsx else _PARSER
    ts_tree = parser.parse(source.encode("utf-8"))
    return TreeAdapter(ts_tree, _wrap_node)


def extract_calls(method_wrapper: _MethodDecl) -> list[str]:
    ts_node = method_wrapper._ts
    calls: list[str] = []
    _walk_calls(ts_node, calls)
    return calls
