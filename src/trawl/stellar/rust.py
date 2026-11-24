import hashlib
import re
from pathlib import PurePosixPath

import tree_sitter_rust
from pydantic import BaseModel, ConfigDict, Field
from tree_sitter import Language, Node, Parser

LANGUAGE = Language(tree_sitter_rust.language())
PARSER = Parser(LANGUAGE)

KNOWN_TYPES = {
    "Address", "Env", "Symbol", "Vec", "Map", "Bytes", "BytesN", "String", "Val",
    "Result", "Option", "bool", "i32", "i64", "i128", "u32", "u64", "u128", "I256",
    "U256", "Duration", "Timepoint", "MuxedAddress", "Error", "Self",
}  # fmt: skip
STRIP = {"line_comment", "block_comment"}
LITERALS = {
    "integer_literal": "N",
    "float_literal": "N",
    "string_literal": "S",
    "raw_string_literal": "S",
    "char_literal": "S",
    "boolean_literal": "B",
}


class RustSettings(BaseModel):
    """
    What counts as a test and which attributes mark a Soroban contract. All fields
    are regexes, matched against the start of an attribute or inside a path.
    """

    model_config = ConfigDict(extra="allow")

    test_file: str = r"(^|/)(tests?|test_snapshots)(/|\.rs$)|_tests?\.rs$|/tests?\.rs$"
    test_attribute: str = r"(?:[\w:]*::)?test\b|cfg\(test\)|cfg\(.*\btest\b"
    contract_impl: str = r"^contractimpl"
    contracttype: str = r"contracttype"
    contracterror: str = r"contracterror"
    contractevent: str = r"contractevent"


DEFAULT = RustSettings()


class Param(BaseModel):
    name: str
    type: str
    attributes: list[str] = Field(default_factory=list)


class FunctionInfo(BaseModel):
    name: str
    line_start: int
    line_end: int
    loc: int
    impl_type: str | None = None
    trait_name: str | None = None
    visibility: str = ""
    attributes: list[str] = Field(default_factory=list)
    params: list[Param] = Field(default_factory=list)
    return_type: str | None = None
    is_contract_fn: bool = False
    is_test: bool = False
    body_hash: str
    shape_hash: str
    shape_nodes: int
    calls: list[str] = Field(default_factory=list)
    macros: list[str] = Field(default_factory=list)
    text: str
    first_byte: int
    last_byte: int


class Member(BaseModel):
    name: str
    types: list[str] = Field(default_factory=list)


class TypeInfo(BaseModel):
    name: str
    line_start: int
    kind: str
    attributes: list[str] = Field(default_factory=list)
    derives: list[str] = Field(default_factory=list)
    is_contracttype: bool = False
    is_contracterror: bool = False
    is_contractevent: bool = False
    is_test: bool = False
    members: list[Member] = Field(default_factory=list)
    shape_hash: str


class ParsedFile(BaseModel):
    path: str
    clean: str
    loc: int
    is_test: bool
    parse_errors: int = 0
    uses: list[str] = Field(default_factory=list)
    macros: list[str] = Field(default_factory=list)
    functions: list[FunctionInfo] = Field(default_factory=list)
    types: list[TypeInfo] = Field(default_factory=list)

    def function_at(self, line: int) -> FunctionInfo | None:
        inside = [f for f in self.functions if f.line_start <= line <= f.line_end]
        return min(inside, key=lambda f: f.line_end - f.line_start, default=None)


def digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def text_of(node: Node | None) -> str:
    return node.text.decode(errors="replace") if node is not None and node.text else ""


def blank_comments(source: bytes, root: Node) -> str:
    cleaned = bytearray(source)
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in STRIP:
            for index in range(node.start_byte, node.end_byte):
                if cleaned[index] != 10:
                    cleaned[index] = 32
            continue
        stack.extend(node.children)
    return cleaned.decode(errors="replace")


def count_errors(root: Node) -> int:
    count, stack = 0, [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            count += 1
        if node.has_error:
            stack.extend(node.children)
    return count


def shape_tokens(node: Node) -> list[str]:
    tokens: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        kind = current.type
        if kind in STRIP or not current.is_named:
            continue
        if kind in LITERALS:
            tokens.append(LITERALS[kind])
            continue
        if kind == "identifier":
            tokens.append("_")
            continue
        if kind == "field_identifier":
            tokens.append(f"f:{text_of(current)}")
            continue
        if kind == "type_identifier":
            name = text_of(current)
            tokens.append(f"t:{name}" if name in KNOWN_TYPES else "T")
            continue
        if kind == "macro_invocation":
            macro = current.child_by_field_name("macro")
            tokens.append(f"m:{text_of(macro)}")
            continue
        tokens.append(kind)
        stack.extend(reversed(current.children))
    return tokens


def attribute_texts(node: Node) -> list[str]:
    found: list[str] = []
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in {"attribute_item", *STRIP}:
        if sibling.type == "attribute_item":
            found.append(text_of(sibling).removeprefix("#[").removesuffix("]").strip())
        sibling = sibling.prev_sibling
    return list(reversed(found))


def first_line_of(node: Node) -> int:
    start = node.start_point.row
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in {"attribute_item", *STRIP}:
        start = sibling.start_point.row
        sibling = sibling.prev_sibling
    return start


def is_test_attribute(attribute: str, settings: RustSettings = DEFAULT) -> bool:
    return bool(re.match(settings.test_attribute, attribute))


def call_names(body: Node) -> tuple[list[str], list[str]]:
    calls: dict[str, None] = {}
    macros: dict[str, None] = {}
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is not None:
                if function.type == "field_expression":
                    calls[text_of(function.child_by_field_name("field"))] = None
                elif function.type == "scoped_identifier":
                    calls[text_of(function.child_by_field_name("name"))] = None
                elif function.type == "identifier":
                    calls[text_of(function)] = None
        elif node.type == "macro_invocation":
            macros[text_of(node.child_by_field_name("macro"))] = None
        stack.extend(node.children)
    return list(calls), list(macros)


def parameters_of(node: Node) -> list[Param]:
    parameters = node.child_by_field_name("parameters")
    found: list[Param] = []
    if parameters is None:
        return found
    pending: list[str] = []
    for child in parameters.children:
        if child.type == "attribute_item":
            pending.append(text_of(child).removeprefix("#[").removesuffix("]").strip())
        elif child.type == "parameter":
            found.append(
                Param(
                    name=text_of(child.child_by_field_name("pattern")),
                    type=text_of(child.child_by_field_name("type")),
                    attributes=pending,
                )
            )
            pending = []
        elif child.type in {"self_parameter"}:
            found.append(Param(name="self", type="Self"))
    return found


def visibility_of(node: Node) -> str:
    for child in node.children:
        if child.type == "visibility_modifier":
            return text_of(child)
    return ""


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def function_info(
    node: Node,
    clean_bytes: bytes,
    impl_type: str | None,
    trait_name: str | None,
    contract_impl: bool,
    in_test: bool,
    settings: RustSettings = DEFAULT,
) -> FunctionInfo:
    attributes = attribute_texts(node)
    body = node.child_by_field_name("body")
    first_row = first_line_of(node)
    first_byte = node.start_byte
    sibling = node.prev_sibling
    while sibling is not None and sibling.type in {"attribute_item", *STRIP}:
        first_byte = sibling.start_byte
        sibling = sibling.prev_sibling
    text = clean_bytes[first_byte : node.end_byte].decode(errors="replace")
    tokens = shape_tokens(body) if body is not None else []
    calls, macros = call_names(body) if body is not None else ([], [])
    visibility = visibility_of(node)
    name = text_of(node.child_by_field_name("name"))
    return_type = node.child_by_field_name("return_type")
    return FunctionInfo(
        name=name,
        line_start=first_row + 1,
        line_end=node.end_point.row + 1,
        loc=sum(1 for line in text.splitlines() if line.strip()),
        impl_type=impl_type,
        trait_name=trait_name,
        visibility=visibility,
        attributes=attributes,
        params=parameters_of(node),
        return_type=text_of(return_type) or None,
        is_contract_fn=contract_impl
        and (visibility.startswith("pub") or bool(trait_name)),
        is_test=in_test or any(is_test_attribute(a, settings) for a in attributes),
        body_hash=digest(normalise(text_of(body))) if body is not None else digest(""),
        shape_hash=digest(" ".join(tokens)),
        shape_nodes=len(tokens),
        calls=calls,
        macros=macros,
        text=text,
        first_byte=first_byte,
        last_byte=node.end_byte,
    )


def type_info(node: Node, in_test: bool, settings: RustSettings = DEFAULT) -> TypeInfo:
    attributes = attribute_texts(node)
    kind = "enum" if node.type == "enum_item" else "struct"
    members: list[Member] = []
    body = node.child_by_field_name("body")
    if body is not None:
        for member in body.children:
            if member.type == "enum_variant":
                inner = member.child_by_field_name("body")
                types = (
                    [
                        text_of(t)
                        for t in inner.named_children
                        if t.type not in {"attribute_item", *STRIP}
                        and t.type != "field_declaration"
                    ]
                    + [
                        text_of(t.child_by_field_name("type"))
                        for t in inner.named_children
                        if t.type == "field_declaration"
                    ]
                    if inner is not None
                    else []
                )
                members.append(
                    Member(
                        name=text_of(member.child_by_field_name("name")), types=types
                    )
                )
            elif member.type == "field_declaration":
                members.append(
                    Member(
                        name=text_of(member.child_by_field_name("name")),
                        types=[text_of(member.child_by_field_name("type"))],
                    )
                )
    derives = [
        part.strip()
        for attribute in attributes
        if attribute.startswith("derive")
        for part in attribute[attribute.find("(") + 1 : attribute.rfind(")")].split(",")
        if part.strip()
    ]
    joined = " ".join(attributes)
    shape = f"{kind}:" + "|".join(",".join(m.types) for m in members)
    return TypeInfo(
        name=text_of(node.child_by_field_name("name")),
        line_start=first_line_of(node) + 1,
        kind=kind,
        attributes=attributes,
        derives=derives,
        is_contracttype=bool(re.search(settings.contracttype, joined)),
        is_contracterror=bool(re.search(settings.contracterror, joined)),
        is_contractevent=bool(re.search(settings.contractevent, joined)),
        is_test=in_test,
        members=members,
        shape_hash=digest(shape),
    )


def parse_rust(
    path: str, source: bytes, settings: RustSettings = DEFAULT
) -> ParsedFile:
    tree = PARSER.parse(source)
    root = tree.root_node
    clean = blank_comments(source, root)
    clean_bytes = clean.encode()
    file_is_test = bool(re.search(settings.test_file, path)) or PurePosixPath(
        path
    ).name in {
        "test.rs",
        "tests.rs",
    }
    parsed = ParsedFile(
        path=path,
        clean=clean,
        loc=sum(1 for line in clean.splitlines() if line.strip()),
        is_test=file_is_test,
        parse_errors=count_errors(root),
    )
    uses: dict[str, None] = {}
    macros: dict[str, None] = {}

    def walk(
        node: Node,
        impl_type: str | None,
        trait_name: str | None,
        contract_impl: bool,
        in_test: bool,
    ) -> None:
        for child in node.children:
            kind = child.type
            if kind == "use_declaration":
                argument = child.child_by_field_name("argument")
                root_name = re.split(r"::|\{", text_of(argument).strip())[0].strip()
                if root_name:
                    uses[root_name] = None
            elif kind == "macro_definition":
                macros[f"def:{text_of(child.child_by_field_name('name'))}"] = None
            elif kind == "macro_invocation":
                macros[text_of(child.child_by_field_name("macro"))] = None
            elif kind == "function_item":
                parsed.functions.append(
                    function_info(
                        child,
                        clean_bytes,
                        impl_type,
                        trait_name,
                        contract_impl,
                        in_test or file_is_test,
                        settings,
                    )
                )
            elif kind == "impl_item":
                attributes = attribute_texts(child)
                walk(
                    child.child_by_field_name("body") or child,
                    text_of(child.child_by_field_name("type")),
                    text_of(child.child_by_field_name("trait")) or None,
                    any(re.match(settings.contract_impl, a) for a in attributes),
                    in_test or any(is_test_attribute(a, settings) for a in attributes),
                )
            elif kind == "mod_item":
                attributes = attribute_texts(child)
                walk(
                    child.child_by_field_name("body") or child,
                    impl_type,
                    trait_name,
                    contract_impl,
                    in_test or any(is_test_attribute(a, settings) for a in attributes),
                )
            elif kind in {"struct_item", "enum_item"}:
                parsed.types.append(type_info(child, in_test or file_is_test, settings))
            elif kind in {"declaration_list", "trait_item"}:
                walk(child, impl_type, trait_name, contract_impl, in_test)

    walk(root, None, None, False, False)
    parsed.uses = list(uses)
    parsed.macros = list(macros)
    return parsed
