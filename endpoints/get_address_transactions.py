# encoding: utf-8
from enum import Enum
from typing import List, Optional
import asyncio
import time
from fastapi import Path, Query, Response, HTTPException
from pydantic import BaseModel
from sqlalchemy import text, func, or_, exists
from sqlalchemy.future import select
from dbsession import async_session
from endpoints import sql_db_only
from endpoints.get_transactions import (
    search_for_transactions,
    TxSearch,
    TxModel,
    PreviousOutpointLookupMode,
    AcceptanceMode,
)
from models.TxAddrMapping import TxAddrMapping
from models.TransactionAcceptance import TransactionAcceptance
from helper.utils import add_cache_control
from constants import ADDRESS_EXAMPLE, PATTERN_VECNO_ADDRESS, GENESIS_MS
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
            pattern=r"^vecno\:[a-z0-9]{61,63}$")):
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
            pattern=r"^vecno\:[a-z0-9]{61,63}$"),
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
            pattern=r"^vecno\:[a-z0-9]{61,63}$"),
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
            pattern=r"^vecno\:[a-z0-9]{61,63}$")
):
    """
    Count the number of transactions associated with this address
    """

    async with async_session() as s:
        count_query = select(func.count()).filter(TxAddrMapping.address == vecnoAddress)

        tx_count = await s.execute(count_query)

    return TransactionCount(total=tx_count.scalar())

@app.get("/stats/transactions", tags=["Stats"])
@sql_db_only
async def get_total_transaction_count():
    """
    Returns the exact total number of transactions on the Vecno network.
    """
    async with async_session() as session:
        total_result = await session.execute(text("SELECT COUNT(*) FROM transactions"))
        total = total_result.scalar_one()

        coinbase_result = await session.execute(
            text("SELECT COUNT(*) FROM transactions WHERE subnetwork_id = '0000000000000000000000000000000000000000'")
        )
        coinbase = coinbase_result.scalar_one()

        regular = total - coinbase

        return {
            "total": int(total),
            "regular": int(regular),
            "timestamp": int(time.time() * 1000),
        }   

@app.get(
    "/stats/transactions/recent-count",
    tags=["Stats"],
    summary="Number of accepted transactions in the last 24 hours",
    description="Returns the count of transactions accepted in blocks "
                "with timestamp within the last 24 hours."
)
@sql_db_only
async def get_recent_transaction_count(response: Response):
    now_ms = int(time.time() * 1000)
    twenty_four_hours_ago_ms = now_ms - 24 * 3600 * 1000

    async with async_session() as s:
        result = await s.execute(text("""
            SELECT COUNT(*) 
            FROM transactions_acceptances ta
            JOIN blocks b ON ta.block_hash = b.hash
            WHERE b.timestamp >= :start_time
              AND NOT EXISTS (
                  SELECT 1 
                  FROM transactions t 
                  WHERE t.transaction_id = ta.transaction_id 
                    AND t.subnetwork_id = '0000000000000000000000000000000000000000'
              )
        """), {"start_time": twenty_four_hours_ago_ms})

        count = result.scalar() or 0

    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"

    return {
        "transactions_last_24h": int(count),
        "period_start_timestamp_ms": twenty_four_hours_ago_ms,
        "period_end_timestamp_ms": now_ms,
        "generated_at_ms": now_ms,
    }
    
@app.get(
    "/addresses/{vecnoAddress}/full-transactions-page",
    response_model=List[TxModel],
    response_model_exclude_unset=True,
    tags=["Vecno addresses"],
    openapi_extra={"strict_query_params": True},
)
@sql_db_only
async def get_full_transactions_for_address_page(
    response: Response,
    vecno_address: str = Path(
        ...,
        alias="vecnoAddress",
        description=f"Vecno address as string e.g. {ADDRESS_EXAMPLE}",
        pattern=PATTERN_VECNO_ADDRESS
    ),
    limit: int = Query(
        description=(
            "The max number of records to get. "
            "For paging combine with using 'before/after' from oldest previous result. "
            "Use value of X-Next-Page-Before/-After as long as header is present to continue paging. "
            "The actual number of transactions returned for each page can be != limit."
        ),
        ge=1,
        le=500,
        default=50,
    ),
    before: int = Query(
        description="Only include transactions with block time before this (epoch-millis)",
        ge=0,
        default=0
    ),
    after: int = Query(
        description="Only include transactions with block time after this (epoch-millis)",
        ge=0,
        default=0
    ),
    fields: str = "",
    resolve_previous_outpoints: PreviousOutpointLookupMode = Query(default="no", description=DESC_RESOLVE_PARAM),
    acceptance: Optional[AcceptanceMode] = Query(default=None),
):
    """
    Get all transactions for a given address from database
    and then return their full transaction data with pagination support.
    """

    query = (
        select(TxAddrMapping.transaction_id, TxAddrMapping.block_time)
        .filter(TxAddrMapping.address == vecno_address)
        .limit(limit)
    )

    response.headers["X-Page-Count"] = "0"

    if before != 0 and after != 0:
        raise HTTPException(status_code=400, detail="Only one of [before, after] can be present")

    if before != 0:
        if before <= GENESIS_MS:
            return []
        query = query.filter(TxAddrMapping.block_time < before).order_by(TxAddrMapping.block_time.desc())

    elif after != 0:
        if after > int(time.time() * 1000) + 3600000:
            return []
        query = query.filter(TxAddrMapping.block_time > after).order_by(TxAddrMapping.block_time.asc())

    else:
        query = query.order_by(TxAddrMapping.block_time.desc())

    if acceptance == AcceptanceMode.accepted:
        query = query.join(
            TransactionAcceptance,
            TxAddrMapping.transaction_id == TransactionAcceptance.transaction_id
        )

    async with async_session() as s:
        result = await s.execute(query)
        tx_ids_and_block_times = [(x.transaction_id, x.block_time) for x in result.all()]

        if not tx_ids_and_block_times:
            return []

        tx_ids_and_block_times.sort(key=lambda x: x[1], reverse=True)

        newest_block_time = tx_ids_and_block_times[0][1]
        oldest_block_time = tx_ids_and_block_times[-1][1]
        tx_ids = {tx_id for tx_id, _ in tx_ids_and_block_times}

        if len(tx_ids_and_block_times) == limit:
            same_block_query = (
                select(TxAddrMapping.transaction_id)
                .filter(TxAddrMapping.address == vecno_address)
                .filter(
                    or_(
                        TxAddrMapping.block_time == newest_block_time,
                        TxAddrMapping.block_time == oldest_block_time,
                    )
                )
            )
            extra_same = await s.execute(same_block_query)
            tx_ids.update(extra_same.scalars().all())

        has_newer = await s.scalar(
            select(
                exists().where(
                    (TxAddrMapping.address == vecno_address)
                    & (TxAddrMapping.block_time > newest_block_time)
                )
            )
        )

        has_older = False
        if not after or after >= GENESIS_MS:
            has_older = await s.scalar(
                select(
                    exists().where(
                        (TxAddrMapping.address == vecno_address)
                        & (TxAddrMapping.block_time < oldest_block_time)
                    )
                )
            )

        if has_newer:
            response.headers["X-Next-Page-After"] = str(newest_block_time)
        if has_older:
            response.headers["X-Next-Page-Before"] = str(oldest_block_time)

        res = await search_for_transactions(
            TxSearch(transactionIds=list(tx_ids), acceptingBlueScores=None),
            fields,
            resolve_previous_outpoints,
            acceptance
        )

        response.headers["X-Page-Count"] = str(len(res))

        if before:
            add_cache_control(None, before, response)
        elif after and len(tx_ids) >= limit:
            max_block_time = max((r.get("block_time") for r in res), default=0)
            add_cache_control(None, max_block_time, response)

        return res