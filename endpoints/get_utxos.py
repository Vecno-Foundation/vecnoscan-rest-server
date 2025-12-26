import asyncio
from typing import List

from fastapi import Path, HTTPException, Response
from pydantic import BaseModel

from server import app, vecnod_client

class OutpointModel(BaseModel):
    transactionId: str
    index: int = 0
    outpointIndex: int | None = None


class ScriptPublicKeyModel(BaseModel):
    scriptPublicKey: str


class UtxoEntryModel(BaseModel):
    amount: str | int
    scriptPublicKey: dict | ScriptPublicKeyModel
    blockDaaScore: str | int
    isCoinbase: bool = False


class UtxoResponse(BaseModel):
    address: str
    outpoint: OutpointModel
    utxoEntry: UtxoEntryModel

_whale_cache: dict[str, tuple[List[UtxoResponse], float]] = {}

NORMAL_TTL = 300
WHALE_THRESHOLD = 50_000
WHALE_TTL = 86_400


def _normalize_utxo(raw: dict, target_address: str) -> UtxoResponse | None:
    if raw.get("address") != target_address:
        return None

    outpoint_raw = raw.get("outpoint", {})
    idx = outpoint_raw.get("index") or outpoint_raw.get("outpointIndex", 0)

    utxo_entry = raw.get("utxoEntry", {})
    spk = utxo_entry.get("scriptPublicKey", {})
    if isinstance(spk, dict):
        spk_str = spk.get("scriptPublicKey", "")
    else:
        spk_str = str(spk)

    spk_str = spk_str.lstrip("0")
    if len(spk_str) % 2 == 1:
        spk_str = "0" + spk_str
    if not spk_str:
        spk_str = "00"

    return UtxoResponse(
        address=target_address,
        outpoint=OutpointModel(
            transactionId=outpoint_raw.get("transactionId", ""),
            index=idx
        ),
        utxoEntry=UtxoEntryModel(
            amount=str(utxo_entry.get("amount", "0")),
            scriptPublicKey=ScriptPublicKeyModel(scriptPublicKey=spk_str),
            blockDaaScore=str(utxo_entry.get("blockDaaScore", "0")),
            isCoinbase=utxo_entry.get("isCoinbase", False)
        )
    )


@app.get(
    "/addresses/{vecnoAddress}/utxos",
    response_model=List[UtxoResponse],
    tags=["Vecno addresses"]
)
async def get_utxos_for_address(
    vecnoAddress: str = Path(pattern=r"^vecno:[a-z0-9]{61,64}$"),
    response: Response = None,
):
    now = asyncio.get_event_loop().time()

    if vecnoAddress in _whale_cache:
        cached_data, ts = _whale_cache[vecnoAddress]
        age = now - ts
        ttl_to_use = WHALE_TTL if len(cached_data) >= WHALE_THRESHOLD else NORMAL_TTL
        if age < ttl_to_use:
            response.headers["X-Cache"] = "HIT"
            response.headers["Cache-Control"] = f"public, max-age={ttl_to_use}"
            if len(cached_data) >= WHALE_THRESHOLD:
                response.headers["X-Whale"] = "true"
            return cached_data
    try:
        raw = await asyncio.wait_for(
            vecnod_client.request(
                "getUtxosByAddressesRequest",
                {"addresses": [vecnoAddress]},
                timeout=90
            ),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Address has extreme number of UTXOs — request timed out after 10s. Using cached data if available."
        )

    entries = raw.get("getUtxosByAddressesResponse", {}).get("entries", [])
    result = [u for u in (_normalize_utxo(e, vecnoAddress) for e in entries) if u is not None]
    count = len(result)

    if count >= WHALE_THRESHOLD:
        ttl = WHALE_TTL
        response.headers["Cache-Control"] = f"public, max-age={ttl}"
        response.headers["X-Whale"] = "true"
        response.headers["X-UTXO-Count"] = str(count)
    else:
        ttl = NORMAL_TTL
        response.headers["Cache-Control"] = "public, max-age=8"

    _whale_cache[vecnoAddress] = (result, now)

    if len(_whale_cache) > 300:
        oldest = min(_whale_cache.items(), key=lambda x: x[1][1])[0]
        del _whale_cache[oldest]

    return result