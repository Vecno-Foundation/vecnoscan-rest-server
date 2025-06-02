# encoding: utf-8
import csv
from io import StringIO
import logging
from fastapi import Path, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List     
from sqlalchemy import asc, desc
from sqlalchemy.future import select

from dbsession import async_session
from endpoints import filter_fields, sql_db_only
from models.Balance import Balance

from server import app, vecnod_client


class BalanceResponse(BaseModel):
    address: str = "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e"
    balance: int = 38240000000


class BalanceResponses(BaseModel):
    balances: List[BalanceResponse]


def csv_generator(balances):
    try:
        yield "Address,Balance\n"
        for balance in balances:
            yield f"{balance.script_public_key_address},{balance.balance}\n"
    except GeneratorExit:
        logging.info("Client disconnected during CSV streaming.")

@app.get("/addresses/{vecnoAddress}/balance", response_model=BalanceResponse, tags=["Vecno addresses"])
async def get_balance_from_vecno_address(
        vecnoAddress: str = Path(
            description="Vecno address as string e.g. vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e",
            regex="^vecno\:[a-z0-9]{61,63}$")):
    """
    Get balance for a given vecno address
    """
    resp = await vecnod_client.request("getBalanceByAddressRequest",
                                       params={
                                           "address": vecnoAddress
                                       })

    try:
        if resp is not None:
            resp = resp["getBalanceByAddressResponse"]
    except KeyError:
        if resp is not None:
            if "getUtxosByAddressesResponse" in resp and "error" in resp["getUtxosByAddressesResponse"]:
                raise HTTPException(status_code=400, detail=resp["getUtxosByAddressesResponse"]["error"])
            else:
                raise
    balance = 0
    try:
        if resp is not None:
            balance = int(resp["balance"])
    except KeyError:
        balance = 0

    return {
        "address": vecnoAddress,
        "balance": balance
    }



@app.get("/addresses/balances/json", response_model=BalanceResponses, tags=["Vecno addresses"])
async def get_balances():
    """
    Get balances of addresses.
    """
    async with async_session() as session:  
        try:
            result = await session.execute(
                select(Balance)
                .order_by(desc(Balance.balance))
            )
            balances = result.scalars().all()
            return BalanceResponses(balances=[
                BalanceResponse(address=balance.script_public_key_address, balance=balance.balance)
                for balance in balances
            ])
        except Exception as e:
            return BalanceResponses(balances=[])



@app.get("/addresses/balances/csv", response_class=StreamingResponse, tags=["Vecno addresses"])
async def get_balances_csv():
    """
    Get balances of addresses in CSV format.
    """
    async with async_session() as session:
        try:
            result = await session.execute(select(Balance).order_by(desc(Balance.balance)))
            balances = result.scalars().all()
            return StreamingResponse(
                csv_generator(balances),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=balances.csv"}
            )
        except Exception as e:
            logging.exception("Error generating CSV:")
            return StreamingResponse(
                iter(["Address,Balance\n"]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=balances.csv"}
            )

@app.get("/addresses/balances/json/paged", response_model=BalanceResponses, tags=["Vecno addresses"])
async def get_balances_paged(page: int = Query(1, ge=1), items_per_page: int = Query(10, ge=1)):
    """
    Get balances of addresses with pagination.
    """
    async with async_session() as session:
        try:
            result = await session.execute(
                select(Balance)
                .offset((page - 1) * items_per_page)
                .limit(items_per_page)
                .order_by(desc(Balance.balance))
            )
            balances = result.scalars().all()
            
            return BalanceResponses(
                balances=[
                    BalanceResponse(address=balance.script_public_key_address, balance=balance.balance)
                    for balance in balances
                ]
            )
        except Exception as e:
            return BalanceResponses(balances=[])


@app.get("/addresses/balances/csv/paged", response_class=StreamingResponse, tags=["Vecno addresses"])
async def get_balances_csv_paged(page: int = Query(1, ge=1), items_per_page: int = Query(10, ge=1)):
    """
    Get balances of addresses in CSV format with pagination.
    """
    async with async_session() as session:
        try:
            result = await session.execute(
                select(Balance)
                .offset((page - 1) * items_per_page)
                .limit(items_per_page)
                .order_by(desc(Balance.balance))
            )
            balances = result.scalars().all()
            return StreamingResponse(
                csv_generator(balances),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=balances.csv"}
            )
        except Exception as e:
            logging.exception("Error generating paged CSV:")
            return StreamingResponse(
                iter(["Address,Balance\n"]),
                media_type="text/csv",
                headers={"Content-Disposition": "attachment; filename=balances.csv"}
            )