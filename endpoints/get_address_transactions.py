# encoding: utf-8
from enum import Enum
from typing import List
import asyncio
from fastapi import Path, Query, Response, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, func, or_
from sqlalchemy.future import select
from models.Transaction import Transaction 
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

@app.get("/stats/transactions", tags=["Stats", "Vecno network"])
@sql_db_only
async def get_total_transaction_count():
    """
    Returns the exact total number of transactions on the Vecno network.
    100% real data — no estimation, no fallback.
    """
    async with async_session() as session:
        # Total transactions
        total_result = await session.execute(text("SELECT COUNT(*) FROM transactions"))
        total = total_result.scalar_one()  # raises if null/no rows

        # Coinbase transactions
        coinbase_result = await session.execute(
            text("SELECT COUNT(*) FROM transactions WHERE subnetwork_id = '0000000000000000000000000000000000000000'")
        )
        coinbase = coinbase_result.scalar_one()

        regular = total - coinbase

        return {
            "total": int(total),
            "regular": int(regular),
            "coinbase": int(coinbase),
            "timestamp": int(asyncio.get_event_loop().time() * 1000),
        }
    
@app.get(
    "/addresses/{vecnoAddress}/full-transactions-page",
    response_model=List[TxModel],
    response_model_exclude_unset=True,
    tags=["Vecno addresses"]
)
@sql_db_only
async def get_full_transactions_for_address_page(
    response: Response,
    vecnoAddress: str = Path(
        description="Vecno address e.g. vecno:qrh6mye34yxkpwefh8n5rx9dq7rngzy5eedrth4mdqjp9qj4g209w29u2telh",
        regex="^vecno\:[a-z0-9]{61,63}$"
    ),
    limit: int = Query(ge=1, le=500, default=50, description="Max records per page"),
    before: int = Query(ge=0, default=0, description="Only txs before this timestamp (ms)"),
    after: int = Query(ge=0, default=0, description="Only txs after this timestamp (ms)"),
    fields: str = "",
    resolve_previous_outpoints: PreviousOutpointLookupMode = Query(default="no", description=DESC_RESOLVE_PARAM),
):
    """
    Paginated full transactions for address with proper X-Next-Page headers.
    Exact same logic as KaspaScan — now working perfectly.
    """
    if before and after:
        raise HTTPException(status_code=400, detail="Cannot use both 'before' and 'after'")

    # Base query
    query = select(TxAddrMapping.transaction_id, TxAddrMapping.block_time) \
        .filter(TxAddrMapping.address == vecnoAddress) \
        .limit(limit + 100)  # extra buffer to avoid gaps

    if before:
        query = query.filter(TxAddrMapping.block_time < before) \
                     .order_by(TxAddrMapping.block_time.desc())
    elif after:
        query = query.filter(TxAddrMapping.block_time > after) \
                     .order_by(TxAddrMapping.block_time.asc())
    else:
        query = query.order_by(TxAddrMapping.block_time.desc())

    async with async_session() as s:
        result = await s.execute(query)
        rows = result.all()

        if not rows:
            response.headers["X-Page-Count"] = "0"
            return []

        # Extract transaction IDs and block times
        tx_data = [(row.transaction_id, row.block_time) for row in rows]
        tx_ids = {tx_id for tx_id, _ in tx_data}
        block_times = [bt for _, bt in tx_data]

        newest_time = max(block_times)
        oldest_time = min(block_times)

        # Handle edge case: same block_time across page boundary
        if len(rows) >= limit:
            extra_query = select(TxAddrMapping.transaction_id) \
                .filter(TxAddrMapping.address == vecnoAddress) \
                .filter(
                    or_(
                        TxAddrMapping.block_time == newest_time,
                        TxAddrMapping.block_time == oldest_time
                    )
                )
            extra_result = await s.execute(extra_query)
            tx_ids.update(extra_result.scalars().all())

    # Set pagination headers
    response.headers["X-Page-Count"] = str(len(tx_ids))
    if len(tx_ids) >= limit:
        response.headers["X-Next-Page-Before"] = str(oldest_time)
        if after:
            response.headers["X-Next-Page-After"] = str(newest_time)

    # Fetch full transaction details
    transactions = await search_for_transactions(
        TxSearch(transactionIds=list(tx_ids)),
        fields,
        resolve_previous_outpoints
    )

    # Sort correctly (newest first, unless using 'after')
    if not after:
        transactions.sort(key=lambda x: x.get("block_time", 0), reverse=True)

    return transactions