import asyncio
import logging
from collections import defaultdict
from enum import Enum
from typing import List, Optional, Dict

from fastapi import Path, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from starlette.responses import Response
from typing import Annotated
from constants import TX_SEARCH_ID_LIMIT, TX_SEARCH_BS_LIMIT, PREV_OUT_RESOLVED, ADDRESS_PREFIX
from dbsession import async_session, async_session_blocks
from endpoints import filter_fields, sql_db_only
from endpoints.get_blocks import get_block_from_vecnod
from helper.utils import add_cache_control
from models.Block import Block
from models.BlockTransaction import BlockTransaction
from models.Subnetwork import Subnetwork
from models.Transaction import Transaction, TransactionOutput, TransactionInput
from models.TransactionAcceptance import TransactionAcceptance
from server import app

_logger = logging.getLogger(__name__)

DESC_RESOLVE_PARAM = (
    "Use this parameter if you want to fetch the TransactionInput previous outpoint details. "
    "Light fetches only the address and amount. Full fetches the whole TransactionOutput and "
    "adds it into each TxInput."
)

class TxOutput(BaseModel):
    transaction_id: str
    index: int
    amount: int
    script_public_key: str | None = None
    script_public_key_address: str | None = None
    script_public_key_type: str | None = None
    accepting_block_hash: str | None = None

    model_config = {"from_attributes": True}


class TxInput(BaseModel):
    transaction_id: str
    index: int
    previous_outpoint_hash: str
    previous_outpoint_index: int
    previous_outpoint_resolved: Optional[TxOutput] = None
    previous_outpoint_address: Optional[str] = None
    previous_outpoint_amount: Optional[int] = None
    signature_script: Optional[str] = None
    sig_op_count: Optional[int] = None

    model_config = {"from_attributes": True}


class TxModel(BaseModel):
    subnetwork_id: Optional[str] = None
    transaction_id: Optional[str] = None
    hash: Optional[str] = None
    mass: Optional[int] = None
    payload: Optional[str] = None
    block_hash: Optional[List[str]] = None
    block_time: Optional[int] = None
    is_accepted: Optional[bool] = None
    accepting_block_hash: Optional[str] = None
    accepting_block_blue_score: Optional[int] = None
    accepting_block_time: Optional[int] = None
    inputs: Optional[List[TxInput]] = None
    outputs: Optional[List[TxOutput]] = None

    model_config = {"from_attributes": True, "extra": "ignore"}


class TxSearchAcceptingBlueScores(BaseModel):
    gte: int
    lt: int


class TxSearch(BaseModel):
    transactionIds: Optional[List[str]] = None
    acceptingBlueScores: Optional[TxSearchAcceptingBlueScores] = None


class TxAcceptanceRequest(BaseModel):
    transactionIds: List[str] = Field(
        example=[
            "b9382bdee4aa364acf73eda93914eaae61d0e78334d1b8a637ab89ef5e224e41",
            "1e098b3830c994beb28768f7924a38286cec16e85e9757e0dc3574b85f624c34",
        ]
    )


class TxAcceptanceResponse(BaseModel):
    transactionId: str
    accepted: bool
    acceptingBlueScore: Optional[int] = None


class PreviousOutpointLookupMode(str, Enum):
    no = "no"
    light = "light"
    full = "full"


class AcceptanceMode(str, Enum):
    accepted = "accepted"
    rejected = "rejected"


class WhaleMovementResponse(BaseModel):
    transaction_id: str
    block_time: int
    amount: int
    from_address: Optional[str] = None
    to_address: str

    model_config = {"from_attributes": True}



# === Helpers ===
def _to_millis(value) -> int | None:
    """Safely convert timestamp to milliseconds (handles int or datetime)"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(value.timestamp() * 1000)


async def get_tx_blocks_from_db(fields: List[str], transaction_ids: List[str]) -> Dict[str, List[str]]:
    result = defaultdict(list)
    if not transaction_ids or (fields and "block_hash" not in fields):
        return result

    async with async_session_blocks() as s:
        rows = await s.execute(
            select(BlockTransaction.transaction_id, BlockTransaction.block_hash)
            .where(BlockTransaction.transaction_id.in_(transaction_ids))
        )
        for tx_id, block_hash in rows.all():
            result[tx_id].append(block_hash)
    return result


async def get_tx_inputs_from_db(
    fields: List[str],
    resolve_mode: PreviousOutpointLookupMode,
    transaction_ids: List[str]
) -> Dict[str, List[TransactionInput]]:
    result = defaultdict(list)
    if not transaction_ids or (fields and "inputs" not in fields):
        return result

    async with async_session() as s:
        if resolve_mode in ("light", "full"):
            query = (
                select(TransactionInput, TransactionOutput)
                .outerjoin(
                    TransactionOutput,
                    (TransactionOutput.transaction_id == TransactionInput.previous_outpoint_hash)
                    & (TransactionOutput.index == TransactionInput.previous_outpoint_index)
                )
                .where(TransactionInput.transaction_id.in_(transaction_ids))
                .order_by(TransactionInput.transaction_id, TransactionInput.index)
            )
            rows = await s.execute(query)
            for inp, prev_out in rows.all():
                if prev_out:
                    inp.previous_outpoint_amount = prev_out.amount
                    inp.previous_outpoint_address = prev_out.script_public_key_address
                    if resolve_mode == "full":
                        inp.previous_outpoint_resolved = prev_out
                result[inp.transaction_id].append(inp)
        else:
            query = (
                select(TransactionInput)
                .where(TransactionInput.transaction_id.in_(transaction_ids))
                .order_by(TransactionInput.transaction_id, TransactionInput.index)
            )
            rows = await s.execute(query)
            for inp in rows.scalars().all():
                result[inp.transaction_id].append(inp)
    return result


async def get_tx_outputs_from_db(fields: List[str], transaction_ids: List[str]) -> Dict[str, List[TransactionOutput]]:
    result = defaultdict(list)
    if not transaction_ids or (fields and "outputs" not in fields):
        return result

    async with async_session() as s:
        rows = await s.execute(
            select(TransactionOutput)
            .where(TransactionOutput.transaction_id.in_(transaction_ids))
            .order_by(TransactionOutput.transaction_id, TransactionOutput.index)
        )
        for out in rows.scalars().all():
            result[out.transaction_id].append(out)
    return result


async def get_transaction_from_vecnod(
    block_hashes: List[str],
    tx_id: str,
    include_inputs: bool,
    include_outputs: bool
) -> Optional[dict]:
    if not block_hashes:
        return None

    block = await get_block_from_vecnod(block_hashes[0], include_transactions=True, include_color=False)
    if not block or "transactions" not in block:
        return None

    for tx in block["transactions"]:
        if tx["verboseData"]["transactionId"] != tx_id:
            continue

        inputs_list = tx.get("inputs", []) or []
        outputs_list = tx.get("outputs", []) or []

        return {
            "subnetwork_id": tx.get("subnetworkId"),
            "transaction_id": tx["verboseData"]["transactionId"],
            "hash": tx["verboseData"].get("hash"),
            "mass": tx["verboseData"].get("computeMass"),
            "payload": tx.get("payload") or None,
            "block_hash": block_hashes,
            "block_time": tx["verboseData"].get("blockTime"),
            "inputs": (
                [
                    {
                        "transaction_id": tx_id,
                        "index": i,
                        "previous_outpoint_hash": inp["previousOutpoint"]["transactionId"],
                        "previous_outpoint_index": inp["previousOutpoint"].get("index", 0),
                        "signature_script": inp.get("signatureScript"),
                        "sig_op_count": inp.get("sigOpCount"),
                    }
                    for i, inp in enumerate(inputs_list)
                ]
                if include_inputs else None
            ),
            "outputs": (
                [
                    {
                        "transaction_id": tx_id,
                        "index": i,
                        "amount": out["amount"],
                        "script_public_key": out["scriptPublicKey"]["scriptPublicKey"],
                        "script_public_key_address": out["verboseData"].get("scriptPublicKeyAddress"),
                        "script_public_key_type": out["verboseData"].get("scriptPublicKeyType"),
                    }
                    for i, out in enumerate(outputs_list)
                ]
                if include_outputs else None
            ),
        }
    return None


@app.get(
    "/transactions/whale-movements",
    response_model=List[WhaleMovementResponse],
    tags=["Vecno transactions"],
    summary="Get Recent Large Transfers",
    description="Returns up to 200 recent large transfers.",
)
@sql_db_only
async def get_whale_movements(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    min_ve: Annotated[int, Query(ge=1, le=100000)] = 1000,
):
    min_amount_sompi = min_ve * 100_000_000

    whale_sql = text("""
        WITH large_outputs AS (
            SELECT DISTINCT ON (o.transaction_id)
                encode(o.transaction_id, 'hex') AS transaction_id,
                o.amount,
                o.script_public_key_address AS to_address,
                t.block_time
            FROM transactions_outputs o
            JOIN transactions t ON o.transaction_id = t.transaction_id
            WHERE o.amount >= :min_amount
              AND t.block_time IS NOT NULL
            ORDER BY o.transaction_id, o.amount DESC
            LIMIT :limit * 5
        ),
        sender_lookup AS (
            SELECT DISTINCT ON (encode(i.transaction_id, 'hex'))
                encode(i.transaction_id, 'hex') AS transaction_id,
                po.script_public_key_address AS from_address
            FROM transactions_inputs i
            JOIN transactions_outputs po
                ON i.previous_outpoint_hash = po.transaction_id
               AND i.previous_outpoint_index = po.index
            WHERE encode(i.transaction_id, 'hex') IN (SELECT transaction_id FROM large_outputs)
            ORDER BY encode(i.transaction_id, 'hex'), po.amount DESC NULLS LAST
        )
        SELECT 
            lo.transaction_id,
            lo.block_time,
            lo.amount,
            sl.from_address,
            lo.to_address
        FROM large_outputs lo
        LEFT JOIN sender_lookup sl ON lo.transaction_id = sl.transaction_id
        ORDER BY lo.amount DESC, lo.block_time DESC
        LIMIT :limit;
    """)

    async with async_session() as s:
        result = await s.execute(
            whale_sql,
            {"min_amount": min_amount_sompi, "limit": limit}
        )
        rows = result.all()

    movements = []
    for row in rows:
        from_addr = f"{ADDRESS_PREFIX}:{row.from_address}" if row.from_address else None
        to_addr = f"{ADDRESS_PREFIX}:{row.to_address}" if row.to_address else None

        movements.append({
            "transaction_id": row.transaction_id,
            "block_time": row.block_time or 0,
            "amount": row.amount,
            "from_address": from_addr,
            "to_address": to_addr,
        })

    response.headers["Cache-Control"] = "public, max-age=8, stale-while-revalidate=20"
    return movements

@app.get(
    "/transactions/{transactionId}",
    response_model=TxModel,
    tags=["Vecno transactions"],
    response_model_exclude_unset=True,
)
@sql_db_only
async def get_transaction(
    response: Response,
    transactionId: str = Path(regex="[a-f0-9]{64}"),
    blockHash: Optional[str] = Query(None, description="Specify a containing block (if known) for faster lookup"),
    inputs: bool = True,
    outputs: bool = True,
    resolve_previous_outpoints: PreviousOutpointLookupMode = Query(
        default=PreviousOutpointLookupMode.no, description=DESC_RESOLVE_PARAM
    ),
):
    block_hashes = [blockHash] if blockHash else []
    if not block_hashes:
        async with async_session_blocks() as s:
            result = await s.execute(
                select(BlockTransaction.block_hash).where(BlockTransaction.transaction_id == transactionId)
            )
            block_hashes = result.scalars().all()

    tx_data = None
    if block_hashes:
        tx_data = await get_transaction_from_vecnod(block_hashes, transactionId, inputs, outputs)

    if not tx_data:
        async with async_session() as s:
            async with async_session_blocks() as s_blocks:
                row = await s.execute(
                    select(Transaction, Subnetwork)
                    .join(Subnetwork, Transaction.subnetwork_id == Subnetwork.id)
                    .where(Transaction.transaction_id == transactionId)
                )
                tx_row = row.first()
                if not tx_row:
                    raise HTTPException(status_code=404, detail="Transaction not found")

                tx, sub = tx_row

                tx_data = {
                    "subnetwork_id": sub.subnetwork_id,
                    "transaction_id": tx.transaction_id,
                    "hash": tx.hash,
                    "mass": tx.mass,
                    "payload": tx.payload or None,
                    "block_hash": block_hashes or None,
                    "block_time": _to_millis(tx.block_time),
                }

                if inputs:
                    inputs_dict = await get_tx_inputs_from_db([], resolve_previous_outpoints, [transactionId])
                    tx_data["inputs"] = inputs_dict.get(transactionId)

                if outputs:
                    outputs_dict = await get_tx_outputs_from_db([], [transactionId])
                    tx_data["outputs"] = outputs_dict.get(transactionId)

    # Acceptance info
    async with async_session() as s:
        acc_row = await s.execute(
            select(TransactionAcceptance.transaction_id, TransactionAcceptance.block_hash)
            .where(TransactionAcceptance.transaction_id == transactionId)
        )
        acc = acc_row.one_or_none()

    tx_data["is_accepted"] = acc is not None
    if acc:
        _, accepting_block_hash = acc
        tx_data["accepting_block_hash"] = accepting_block_hash

        async with async_session_blocks() as s_blocks:
            block_row = await s_blocks.execute(
                select(Block.blue_score, Block.timestamp).where(Block.hash == accepting_block_hash)
            )
            block_info = block_row.one_or_none()

            if block_info:
                tx_data["accepting_block_blue_score"] = block_info.blue_score
                tx_data["accepting_block_time"] = _to_millis(block_info.timestamp)
            else:
                # Fallback to vecnod
                block = await get_block_from_vecnod(accepting_block_hash, False, False)
                if block and block.get("header"):
                    header = block["header"]
                    tx_data["accepting_block_blue_score"] = int(header.get("blueScore") or 0)
                    tx_data["accepting_block_time"] = int(header.get("timestamp") or 0)

    add_cache_control(
        tx_data.get("accepting_block_blue_score"),
        tx_data.get("block_time"),
        response
    )
    return tx_data


@app.post(
    "/transactions/search",
    response_model=List[TxModel],
    tags=["Vecno transactions"],
    response_model_exclude_unset=True
)
@sql_db_only
async def search_for_transactions(
    txSearch: TxSearch,
    fields: str = Query(default=""),
    resolve_previous_outpoints: PreviousOutpointLookupMode = Query(
        default=PreviousOutpointLookupMode.no, description=DESC_RESOLVE_PARAM
    ),
    acceptance: Optional[AcceptanceMode] = None,
):
    if not txSearch.transactionIds and not txSearch.acceptingBlueScores:
        return []

    if txSearch.transactionIds and len(txSearch.transactionIds) > TX_SEARCH_ID_LIMIT:
        raise HTTPException(422, f"Too many transaction ids. Max {TX_SEARCH_ID_LIMIT}")

    if txSearch.transactionIds and txSearch.acceptingBlueScores:
        raise HTTPException(422, "Only one of transactionIds and acceptingBlueScores must be provided")

    if txSearch.acceptingBlueScores and (txSearch.acceptingBlueScores.lt - txSearch.acceptingBlueScores.gte > TX_SEARCH_BS_LIMIT):
        raise HTTPException(400, f"Range too large. Max {TX_SEARCH_BS_LIMIT}")

    fields_list = fields.split(",") if fields else []

    async with async_session() as s:
        async with async_session_blocks() as s_blocks:
            query = (
                select(
                    Transaction,
                    Subnetwork,
                    TransactionAcceptance.transaction_id.label("accepted_id"),
                    TransactionAcceptance.block_hash.label("accepting_block_hash"),
                )
                .join(Subnetwork, Transaction.subnetwork_id == Subnetwork.id)
                .outerjoin(TransactionAcceptance, Transaction.transaction_id == TransactionAcceptance.transaction_id)
                .order_by(Transaction.block_time.desc())
            )

            block_map = {}
            if txSearch.acceptingBlueScores:
                gte, lt = txSearch.acceptingBlueScores.gte, txSearch.acceptingBlueScores.lt
                blocks = await s_blocks.execute(
                    select(Block.hash, Block.blue_score, Block.timestamp)
                    .where(Block.blue_score >= gte)
                    .where(Block.blue_score < lt)
                )
                block_map = {b.hash: (b.blue_score, _to_millis(b.timestamp)) for b in blocks.all()}
                if not block_map:
                    return []
                query = query.where(TransactionAcceptance.block_hash.in_(block_map.keys()))
            else:
                query = query.where(Transaction.transaction_id.in_(txSearch.transactionIds or []))
                if acceptance == AcceptanceMode.accepted:
                    query = query.where(TransactionAcceptance.transaction_id.is_not(None))
                elif acceptance == AcceptanceMode.rejected:
                    query = query.where(TransactionAcceptance.transaction_id.is_(None))

            rows = await s.execute(query)
            tx_rows = rows.all()

    if not tx_rows:
        return []

    tx_ids = [row.Transaction.transaction_id for row in tx_rows]

    blocks_dict, inputs_dict, outputs_dict = await asyncio.gather(
        get_tx_blocks_from_db(fields_list, tx_ids),
        get_tx_inputs_from_db(fields_list, resolve_previous_outpoints, tx_ids),
        get_tx_outputs_from_db(fields_list, tx_ids),
    )

    results = []
    for tx, sub, _, accepting_block_hash in tx_rows:
        bs = ts = None
        if accepting_block_hash and txSearch.acceptingBlueScores:
            bs, ts = block_map.get(accepting_block_hash, (None, None))

        result = filter_fields({
            "subnetwork_id": sub.subnetwork_id,
            "transaction_id": tx.transaction_id,
            "hash": tx.hash,
            "mass": tx.mass,
            "payload": tx.payload,
            "block_hash": blocks_dict.get(tx.transaction_id),
            "block_time": _to_millis(tx.block_time),
            "is_accepted": accepting_block_hash is not None,
            "accepting_block_hash": accepting_block_hash,
            "accepting_block_blue_score": bs,
            "accepting_block_time": ts,
            "inputs": inputs_dict.get(tx.transaction_id),
            "outputs": outputs_dict.get(tx.transaction_id),
        }, fields_list)
        results.append(result)

    return results


@app.post(
    "/transactions/acceptance",
    response_model=List[TxAcceptanceResponse],
    tags=["Vecno transactions"],
)
@sql_db_only
async def get_transaction_acceptance(request: TxAcceptanceRequest):
    tx_ids = request.transactionIds
    if len(tx_ids) > TX_SEARCH_ID_LIMIT:
        raise HTTPException(422, f"Too many transaction ids. Max {TX_SEARCH_ID_LIMIT}")

    async with async_session() as s:
        rows = await s.execute(
            select(TransactionAcceptance.transaction_id, TransactionAcceptance.block_hash)
            .where(TransactionAcceptance.transaction_id.in_(tx_ids))
        )
        accepted = dict(rows.all())

    async with async_session_blocks() as s:
        if accepted:
            rows = await s.execute(
                select(Block.hash, Block.blue_score)
                .where(Block.hash.in_(list(accepted.values())))
            )
            blue_scores = dict(rows.all())
        else:
            blue_scores = {}

    return [
        TxAcceptanceResponse(
            transactionId=tx_id,
            accepted=tx_id in accepted,
            acceptingBlueScore=blue_scores.get(accepted.get(tx_id))
        )
        for tx_id in tx_ids
    ]