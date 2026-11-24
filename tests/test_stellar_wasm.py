from stellar_sdk import xdr

from trawl.stellar.wasm import WasmSettings, custom_sections, read_wasm


def leb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def meta(key: str, value: str) -> bytes:
    entry = xdr.SCMetaEntry(
        kind=xdr.SCMetaKind.SC_META_V0,
        v0=xdr.SCMetaV0(key=key.encode(), val=value.encode()),
    )
    return entry.to_xdr_bytes()


def module(section: str, payload: bytes) -> bytes:
    name = section.encode()
    body = leb(len(name)) + name + payload
    return b"\x00asm\x01\x00\x00\x00" + b"\x00" + leb(len(body)) + body


WASM = module("contractmetav0", meta("rssdkver", "22.0.7#abc") + meta("rsver", "1.84"))


def test_custom_sections_are_found_by_name():
    assert "contractmetav0" in custom_sections(WASM)


def test_the_sdk_and_rust_versions_come_from_the_meta_section():
    info = read_wasm(WASM)

    assert info.size == len(WASM)
    assert info.sdk_version == "22.0.7"
    assert info.rust_version == "1.84"


def test_the_meta_keys_and_section_names_are_regex_settings():
    other = module("custommeta", meta("toolchain-version", "9.9#x"))
    settings = WasmSettings(meta_section="custommeta", sdk_key=r"toolchain-.*")

    assert read_wasm(other, settings).sdk_version == "9.9"
    assert read_wasm(other).sdk_version is None
