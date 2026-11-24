import base64
import re

CONTRACT = re.compile(r"\bC[A-Z2-7]{55}\b")
CONTRACT_VERSION = 2 << 3
ACCOUNT = re.compile(r"\bG[A-Z2-7]{55}\b")
ACCOUNT_VERSION = 6 << 3


def crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (
                ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
            )
    return crc


def is_strkey(value: str, version: int = CONTRACT_VERSION) -> bool:
    try:
        raw = base64.b32decode(value)
    except ValueError:
        return False
    return (
        len(raw) == 35
        and raw[0] == version
        and int.from_bytes(raw[33:], "little") == crc16(raw[:33])
    )


def is_contract_id(value: str) -> bool:
    return is_strkey(value, CONTRACT_VERSION)


def find_strkeys(
    text: str,
    pattern: re.Pattern[str] = CONTRACT,
    version: int = CONTRACT_VERSION,
) -> list[tuple[str, int]]:
    """Valid keys in the text with their line number. Pass ACCOUNT and ACCOUNT_VERSION for G addresses."""
    found: list[tuple[str, int]] = []
    for match in pattern.finditer(text):
        if is_strkey(match.group(0), version):
            found.append((match.group(0), text.count("\n", 0, match.start()) + 1))
    return found


def find_contract_ids(text: str) -> list[tuple[str, int]]:
    return find_strkeys(text)
