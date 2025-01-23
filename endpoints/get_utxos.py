# encoding: utf-8
from typing import List

from fastapi import Path, HTTPException
from pydantic import BaseModel

from constants import REGEX_VECNO_ADDRESS, ADDRESS_EXAMPLE
from server import app, vecnod_client


class OutpointModel(BaseModel):
    transactionId: str = "ef62efbc2825d3ef9ec1cf9b80506876ac077b64b11a39c8ef5e028415444dc9"
    index: int = 0


class ScriptPublicKeyModel(BaseModel):
    scriptPublicKey: str = "20c5629ce85f6618cd3ed1ac1c99dc6d3064ed244013555c51385d9efab0d0072fac"


class UtxoModel(BaseModel):
    amount: str = ("11501593788",)
    scriptPublicKey: ScriptPublicKeyModel
    blockDaaScore: str = "18867232"
    isCoinbase: bool = False


class UtxoResponse(BaseModel):
    address: str = ADDRESS_EXAMPLE
    outpoint: OutpointModel
    utxoEntry: UtxoModel


@app.get("/addresses/{vecnoAddress}/utxos", response_model=List[UtxoResponse], tags=["Vecno addresses"])
async def get_utxos_for_address(
    vecnoAddress: str = Path(description=f"Vecno address as string e.g. {ADDRESS_EXAMPLE}", regex=REGEX_VECNO_ADDRESS),
):
    """
    Lists all open utxo for a given vecno address
    """
    resp = await vecnod_client.request("getUtxosByAddressesRequest", params={"addresses": [vecnoAddress]}, timeout=120)
    try:
        if "getUtxosByAddressesResponse" in resp and "error" in resp["getUtxosByAddressesResponse"]:
            raise HTTPException(status_code=400, detail=resp["getUtxosByAddressesResponse"]["error"])

        return (utxo for utxo in resp["getUtxosByAddressesResponse"]["entries"] if utxo["address"] == vecnoAddress)
    except KeyError:
        return []


class UtxoRequest(BaseModel):
    addresses: list[str] = [ADDRESS_EXAMPLE]


@app.post("/addresses/utxos", response_model=List[UtxoResponse], tags=["Vecno addresses"])
async def get_utxos_for_addresses(body: UtxoRequest):
    """
    Lists all open utxo for a given vecno address
    """
    resp = await vecnod_client.request("getUtxosByAddressesRequest", params={"addresses": body.addresses}, timeout=120)
    try:
        if "getUtxosByAddressesResponse" in resp and "error" in resp["getUtxosByAddressesResponse"]:
            raise HTTPException(status_code=400, detail=resp["getUtxosByAddressesResponse"]["error"])

        return (utxo for utxo in resp["getUtxosByAddressesResponse"]["entries"])
    except KeyError:
        return []
