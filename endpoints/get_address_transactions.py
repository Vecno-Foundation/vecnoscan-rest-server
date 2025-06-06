# encoding: utf-8
from enum import Enum
from typing import List

from fastapi import Path, Query
from pydantic import BaseModel
from sqlalchemy import text, func
from sqlalchemy.future import select

from dbsession import async_session
from endpoints import sql_db_only
from endpoints.get_transactions import search_for_transactions, TxSearch, TxModel
from models.TxAddrMapping import TxAddrMapping
from server import app

DESC_RESOLVE_PARAM = "Use this parameter if you want to fetch the TransactionInput previous outpoint details." \
                     " Light fetches only the address and amount. Full fetches the whole TransactionOutput and " \
                     "adds it into each TxInput."


class TransactionsReceivedAndSpent(BaseModel):
    tx_received: str
    tx_spent: str | None
    # received_amount: int = 38240000000


class TransactionForAddressResponse(BaseModel):
    transactions: List[TransactionsReceivedAndSpent]


class TransactionCount(BaseModel):
    total: int


class PreviousOutpointLookupMode(str, Enum):
    no = "no"
    light = "light"
    full = "full"


@app.get("/addresses/{vecnoAddress}/transactions",
         response_model=TransactionForAddressResponse,
         response_model_exclude_unset=True,
         tags=["Vecno addresses"],
         deprecated=False)
@sql_db_only
async def get_transactions_for_address(
        vecnoAddress: str = Path(
            description="Vecno address as string e.g. "
                        "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e",
            regex="^vecno\:[a-z0-9]{61,63}$")):
    """
    Get all transactions for a given address from database
    """
    # SELECT transactions_outputs.transaction_id, transactions_inputs.transaction_id as inp_transaction FROM transactions_outputs
    #
    # LEFT JOIN transactions_inputs ON transactions_inputs.previous_outpoint_hash = transactions_outputs.transaction_id AND transactions_inputs.previous_outpoint_index::int = transactions_outputs.index
    #
    # WHERE "script_public_key_address" = 'vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e'
    #
    # ORDER by transactions_outputs.transaction_id
    async with async_session() as session:
        resp = await session.execute(text(f"""
            SELECT transactions_outputs.transaction_id, transactions_outputs.index, transactions_inputs.transaction_id as inp_transaction,
                    transactions.block_time, transactions.transaction_id
            
            FROM transactions
			LEFT JOIN transactions_outputs ON transactions.transaction_id = transactions_outputs.transaction_id
			LEFT JOIN transactions_inputs ON transactions_inputs.previous_outpoint_hash = transactions.transaction_id AND transactions_inputs.previous_outpoint_index = transactions_outputs.index
            WHERE "script_public_key_address" = :vecnoAddress
			ORDER by transactions.block_time DESC
			LIMIT 500"""),
                                     {'vecnoAddress': vecnoAddress})

        resp = resp.all()

    # build response
    tx_list = []
    for x in resp:
        tx_list.append({"tx_received": x[0],
                        "tx_spent": x[2]})
    return {
        "transactions": tx_list
    }


@app.get("/addresses/{vecnoAddress}/full-transactions",
        response_model=List[TxModel],
        response_model_exclude_unset=True,
        tags=["Vecno addresses"])
@sql_db_only
async def get_full_transactions_for_address(
        vecnoAddress: str = Path(
            description="Vecno address as string e.g. "
                        "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e",
            regex="^vecno\:[a-z0-9]{61,63}$"),
        limit: int = Query(
            description="The number of records to get",
            ge=1,
            le=500,
            default=50),
        offset: int = Query(
            description="The offset from which to get records",
            ge=0,
            default=0),
        fields: str = "",
        resolve_previous_outpoints: PreviousOutpointLookupMode =
        Query(default="no",
            description=DESC_RESOLVE_PARAM)):
    """
    Get all transactions for a given address from database.
    And then get their related full transaction data
    """

    async with async_session() as s:
        # Doing it this way as opposed to adding it directly in the IN clause
        # so I can re-use the same result in tx_list, TxInput and TxOutput
        tx_within_limit_offset = await s.execute(select(TxAddrMapping.transaction_id)
                                                .filter(TxAddrMapping.address == vecnoAddress)
                                                .limit(limit)
                                                .offset(offset)
                                                .order_by(TxAddrMapping.block_time.desc())
                                                )

        tx_ids_in_page = [x[0] for x in tx_within_limit_offset.all()]

    return await search_for_transactions(TxSearch(transactionIds=tx_ids_in_page),
                                        fields,
                                        resolve_previous_outpoints)
    

@app.get("/addresses/{vecnoAddress}/full-transactions/paged",
         response_model=List[TxModel],
         response_model_exclude_unset=True,
         tags=["Vecno addresses"])
@sql_db_only
async def get_full_transactions_for_address_paged(
        vecnoAddress: str = Path(
            description="Vecno address as string e.g. "
                        "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e",
            regex="^vecno\:[a-z0-9]{61,63}$"),
        page: int = Query(
            description="Page number (1-based)",
            ge=1,
            default=1),
        items_per_page: int = Query(
            description="Number of records per page",
            ge=1,
            le=500,
            default=50),
        fields: str = "",
        resolve_previous_outpoints: PreviousOutpointLookupMode =
        Query(default="no",
              description=DESC_RESOLVE_PARAM)):
    """
    Get all transactions for a given address from database.
    Paginated using page number and items per page.
    """

    offset = (page - 1) * items_per_page

    async with async_session() as s:
        tx_within_page = await s.execute(select(TxAddrMapping.transaction_id)
                                         .filter(TxAddrMapping.address == vecnoAddress)
                                         .limit(items_per_page)
                                         .offset(offset)
                                         .order_by(TxAddrMapping.block_time.desc()))

        tx_ids_in_page = [x[0] for x in tx_within_page.all()]

    return await search_for_transactions(TxSearch(transactionIds=tx_ids_in_page),
                                         fields,
                                         resolve_previous_outpoints)


@app.get("/addresses/{vecnoAddress}/transactions-count",
         response_model=TransactionCount,
         tags=["Vecno addresses"])
@sql_db_only
async def get_transaction_count_for_address(
        vecnoAddress: str = Path(
            description="Vecno address as string e.g. "
                        "vecno:qqtsqwxa3q4aw968753rya4tazahmr7jyn5zu7vkncqlvk2aqlsdsah9ut65e",
            regex="^vecno\:[a-z0-9]{61,63}$")
):
    """
    Count the number of transactions associated with this address
    """

    async with async_session() as s:
        count_query = select(func.count()).filter(TxAddrMapping.address == vecnoAddress)

        tx_count = await s.execute(count_query)

    return TransactionCount(total=tx_count.scalar())
