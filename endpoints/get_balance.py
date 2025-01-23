# encoding: utf-8

from fastapi import Path, HTTPException
from pydantic import BaseModel

from constants import ADDRESS_EXAMPLE, REGEX_VECNO_ADDRESS
from server import app, vecnod_client


class BalanceResponse(BaseModel):
    address: str = ADDRESS_EXAMPLE
    balance: int = 38240000000


@app.get("/addresses/{vecnoAddress}/balance", response_model=BalanceResponse, tags=["Vecno addresses"])
async def get_balance_from_vecno_address(
    vecnoAddress: str = Path(description=f"Vecno address as string e.g. {ADDRESS_EXAMPLE}", regex=REGEX_VECNO_ADDRESS),
):
    """
    Get balance for a given vecno address
    """
    resp = await vecnod_client.request("getBalanceByAddressRequest", params={"address": vecnoAddress})

    try:
        resp = resp["getBalanceByAddressResponse"]
    except KeyError:
        if "getUtxosByAddressesResponse" in resp and "error" in resp["getUtxosByAddressesResponse"]:
            raise HTTPException(status_code=400, detail=resp["getUtxosByAddressesResponse"]["error"])
        else:
            raise

    if resp.get("error"):
        raise HTTPException(500, resp["error"])

    try:
        balance = int(resp["balance"])

    # return 0 if address is ok, but no utxos there
    except KeyError:
        balance = 0

    return {"address": vecnoAddress, "balance": balance}
