import asyncio
import itertools
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict
from stellar_sdk import Address, xdr


class RpcSettings(BaseModel):
    """How the client talks to the endpoints. Extra fields are kept."""

    model_config = ConfigDict(extra="allow")

    retries: int = 5
    timeout: float = 90
    rate_limit_pause: float = 2
    backoff: float = 2


DEFAULT = RpcSettings()


class Entry(BaseModel):
    key: str
    xdr: str
    live_until: int | None = None


class Instance(BaseModel):
    contract_id: str
    executable: str
    wasm_hash: str | None
    live_until: int | None


class RpcError(Exception):
    pass


class RpcClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        urls: list[str],
        settings: RpcSettings = DEFAULT,
    ) -> None:
        self.http = http
        self.settings = settings
        self.urls = itertools.cycle(urls)
        self.size = len(urls)
        self.latest = 0

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async def attempt() -> Any:
            last: Exception | None = None
            for _ in range(self.size):
                url = next(self.urls)
                try:
                    response = await self.http.post(
                        url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": method,
                            "params": params or {},
                        },
                        timeout=self.settings.timeout,
                    )
                    if response.status_code == 429:
                        await asyncio.sleep(self.settings.rate_limit_pause)
                        continue
                    body = response.json()
                except (httpx.HTTPError, ValueError) as error:
                    last = error
                    continue
                if "error" in body:
                    last = RpcError(str(body["error"])[:200])
                    continue
                return body["result"]
            if isinstance(last, httpx.TransportError | httpx.TimeoutException):
                raise last
            raise RpcError(str(last))

        for pause in range(self.settings.retries - 1):
            try:
                return await attempt()
            except (httpx.TransportError, httpx.TimeoutException):
                await asyncio.sleep(self.settings.backoff**pause)
        return await attempt()

    async def entries(self, keys: list[str]) -> dict[str, Entry]:
        result = await self.call("getLedgerEntries", {"keys": keys})
        self.latest = max(self.latest, int(result.get("latestLedger", 0)))
        return {
            e["key"]: Entry(
                key=e["key"], xdr=e["xdr"], live_until=e.get("liveUntilLedgerSeq")
            )
            for e in result.get("entries") or []
        }


def instance_key(contract_id: str) -> str:
    return xdr.LedgerKey(
        type=xdr.LedgerEntryType.CONTRACT_DATA,
        contract_data=xdr.LedgerKeyContractData(
            contract=Address(contract_id).to_xdr_sc_address(),
            key=xdr.SCVal(xdr.SCValType.SCV_LEDGER_KEY_CONTRACT_INSTANCE),
            durability=xdr.ContractDataDurability.PERSISTENT,
        ),
    ).to_xdr()


def code_key(wasm_hash: str) -> str:
    return xdr.LedgerKey(
        type=xdr.LedgerEntryType.CONTRACT_CODE,
        contract_code=xdr.LedgerKeyContractCode(
            hash=xdr.Hash(bytes.fromhex(wasm_hash))
        ),
    ).to_xdr()


def read_instance(contract_id: str, entry: Entry) -> Instance:
    data = xdr.LedgerEntryData.from_xdr(entry.xdr)
    executable = data.contract_data.val.instance.executable
    wasm = executable.type == xdr.ContractExecutableType.CONTRACT_EXECUTABLE_WASM
    return Instance(
        contract_id=contract_id,
        executable="wasm" if wasm else "stellar_asset",
        wasm_hash=executable.wasm_hash.hash.hex() if wasm else None,
        live_until=entry.live_until,
    )


def read_code(entry: Entry) -> bytes:
    return xdr.LedgerEntryData.from_xdr(entry.xdr).contract_code.code
