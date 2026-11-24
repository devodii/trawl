import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from stellar_sdk import xdr
from xdrlib3 import Unpacker


class WasmSettings(BaseModel):
    """Which custom sections and meta keys to read. Keys are regexes matched in full."""

    model_config = ConfigDict(extra="allow")

    meta_section: str = "contractmetav0"
    spec_section: str = "contractspecv0"
    sdk_key: str = r"rssdkver"
    rust_key: str = r"rsver"
    sdk_split: str = "#"


DEFAULT = WasmSettings()


class WasmInfo(BaseModel):
    size: int
    sdk_version: str | None = None
    rust_version: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)
    spec: dict[str, Any] = Field(default_factory=dict)

    @property
    def function_names(self) -> list[str]:
        return [f["name"] for f in self.spec.get("functions", [])]


def leb128(data: bytes, index: int) -> tuple[int, int]:
    value, shift = 0, 0
    while True:
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, index


def custom_sections(wasm: bytes) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    index = 8
    while index < len(wasm):
        section = wasm[index]
        index += 1
        size, index = leb128(wasm, index)
        body = wasm[index : index + size]
        index += size
        if section == 0:
            length, offset = leb128(body, 0)
            found[body[offset : offset + length].decode(errors="replace")] = body[
                offset + length :
            ]
    return found


def entries(data: bytes, kind: type) -> list:
    unpacker = Unpacker(data)
    items = []
    while unpacker.get_position() < len(data):
        items.append(kind.unpack(unpacker))
    return items


def text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    if hasattr(value, "sc_symbol"):
        return value.sc_symbol.decode(errors="replace")
    return str(value)


def type_name(definition: xdr.SCSpecTypeDef) -> str:
    kind = definition.type
    name = kind.name.removeprefix("SC_SPEC_TYPE_").lower()
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_OPTION:
        return f"option<{type_name(definition.option.value_type)}>"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_RESULT:
        inner = definition.result
        return f"result<{type_name(inner.ok_type)}, {type_name(inner.error_type)}>"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_VEC:
        return f"vec<{type_name(definition.vec.element_type)}>"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_MAP:
        inner = definition.map
        return f"map<{type_name(inner.key_type)}, {type_name(inner.value_type)}>"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_TUPLE:
        return "(" + ", ".join(type_name(t) for t in definition.tuple.value_types) + ")"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_BYTES_N:
        return f"bytes{definition.bytes_n.n}"
    if kind == xdr.SCSpecType.SC_SPEC_TYPE_UDT:
        return text(definition.udt.name)
    return name


def decode_spec(data: bytes) -> dict[str, Any]:
    spec: dict[str, list] = {
        "functions": [],
        "structs": [],
        "unions": [],
        "enums": [],
        "errors": [],
        "events": [],
    }
    for entry in entries(data, xdr.SCSpecEntry):
        kind = entry.kind
        if kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0:
            function = entry.function_v0
            spec["functions"].append(
                {
                    "name": text(function.name),
                    "inputs": [
                        {"name": text(i.name), "type": type_name(i.type)}
                        for i in function.inputs
                    ],
                    "outputs": [type_name(o) for o in function.outputs],
                }
            )
        elif kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0:
            struct = entry.udt_struct_v0
            spec["structs"].append(
                {
                    "name": text(struct.name),
                    "fields": [
                        {"name": text(f.name), "type": type_name(f.type)}
                        for f in struct.fields
                    ],
                }
            )
        elif kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_UNION_V0:
            union = entry.udt_union_v0
            spec["unions"].append(
                {
                    "name": text(union.name),
                    "cases": [
                        text(
                            case.void_case.name
                            if case.kind
                            == xdr.SCSpecUDTUnionCaseV0Kind.SC_SPEC_UDT_UNION_CASE_VOID_V0
                            else case.tuple_case.name
                        )
                        for case in union.cases
                    ],
                }
            )
        elif kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ENUM_V0:
            enum = entry.udt_enum_v0
            spec["enums"].append(
                {
                    "name": text(enum.name),
                    "cases": [
                        {"name": text(c.name), "value": c.value.uint32}
                        for c in enum.cases
                    ],
                }
            )
        elif kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0:
            errors = entry.udt_error_enum_v0
            spec["errors"].append(
                {
                    "name": text(errors.name),
                    "cases": [
                        {"name": text(c.name), "value": c.value.uint32}
                        for c in errors.cases
                    ],
                }
            )
        elif kind == xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0:
            event = entry.event_v0
            spec["events"].append(
                {
                    "name": text(event.name),
                    "topics": [text(t) for t in event.prefix_topics],
                    "params": [
                        {"name": text(p.name), "type": type_name(p.type)}
                        for p in event.params
                    ],
                }
            )
    return spec


def pick(meta: dict[str, str], pattern: str) -> str | None:
    return next((v for k, v in meta.items() if re.fullmatch(pattern, k)), None)


def read_wasm(wasm: bytes, settings: WasmSettings = DEFAULT) -> WasmInfo:
    sections = custom_sections(wasm)
    meta: dict[str, str] = {}
    if settings.meta_section in sections:
        for entry in entries(sections[settings.meta_section], xdr.SCMetaEntry):
            meta[text(entry.v0.key)] = text(entry.v0.val)
    spec: dict[str, Any] = {}
    if settings.spec_section in sections:
        try:
            spec = decode_spec(sections[settings.spec_section])
        except Exception:
            spec = {}
    sdk = pick(meta, settings.sdk_key) or ""
    return WasmInfo(
        size=len(wasm),
        sdk_version=sdk.split(settings.sdk_split)[0] or None,
        rust_version=pick(meta, settings.rust_key),
        meta=meta,
        spec=spec,
    )
