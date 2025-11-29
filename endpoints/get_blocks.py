# encoding: utf-8
import logging
import os
from typing import List, Optional
from fastapi import Query, Path, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select, exists, func
from constants import BPS
from dbsession import async_session, async_session_blocks
from endpoints.get_virtual_chain_blue_score import current_blue_score_data
from helper.mining_address import get_miner_payload_from_block, retrieve_miner_info_from_payload
from helper.difficulty_calculation import bits_to_difficulty
from helper.utils import add_cache_control
from models.Block import Block
from models.BlockParent import BlockParent
from models.BlockTransaction import BlockTransaction
from models.Subnetwork import Subnetwork
from models.Transaction import TransactionOutput, TransactionInput, Transaction
from models.TransactionAcceptance import TransactionAcceptance
from server import app, vecnod_client

_logger = logging.getLogger(__name__)

IS_SQL_DB_CONFIGURED = os.getenv("SQL_URI") is not None

class VerboseDataModel(BaseModel):
    hash: str
    difficulty: float | None = None
    selectedParentHash: str | None = None
    transactionIds: List[str] | None = None
    blueScore: str | None = None
    childrenHashes: List[str] | None = None
    mergeSetBluesHashes: List[str] | None = None
    mergeSetRedsHashes: List[str] | None = None
    isChainBlock: bool | None = None


class ParentHashModel(BaseModel):
    parentHashes: List[str]


class BlockHeader(BaseModel):
    version: int | None = None
    hashMerkleRoot: str | None = None
    acceptedIdMerkleRoot: str | None = None
    utxoCommitment: str | None = None
    timestamp: str | None = None
    bits: int | None = None
    nonce: str | None = None
    daaScore: str | None = None
    blueWork: str | None = None
    parents: List[ParentHashModel] | None = None
    blueScore: str | None = None
    pruningPoint: str | None = None


class BlockTxInputPreviousOutpointModel(BaseModel):
    transactionId: str
    index: int


class BlockTxInputModel(BaseModel):
    previousOutpoint: Optional[BlockTxInputPreviousOutpointModel] = None
    signatureScript: str | None = None
    sigOpCount: int | None = None
    sequence: int | None = None


class BlockTxOutputScriptPublicKeyModel(BaseModel):
    scriptPublicKey: str | None = None
    version: int | None = None


class BlockTxOutputVerboseDataModel(BaseModel):
    scriptPublicKeyType: str | None = None
    scriptPublicKeyAddress: str | None = None


class BlockTxOutputModel(BaseModel):
    amount: int | None = None
    scriptPublicKey: Optional[BlockTxOutputScriptPublicKeyModel] = None
    verboseData: Optional[BlockTxOutputVerboseDataModel] = None


class BlockTxVerboseDataModel(BaseModel):
    transactionId: str
    hash: str | None = None
    computeMass: int | None = None
    blockHash: str | None = None
    blockTime: int | None = None


class BlockTxModel(BaseModel):
    inputs: List[BlockTxInputModel] | None = None
    outputs: List[BlockTxOutputModel] | None = None
    subnetworkId: str | None = None
    payload: str | None = None
    verboseData: BlockTxVerboseDataModel
    lockTime: int | None = None
    gas: int | None = None
    mass: int | None = None
    version: int | None = None


class ExtraModel(BaseModel):
    color: str | None = None
    minerAddress: str | None = None
    minerInfo: str | None = None


class BlockModel(BaseModel):
    header: BlockHeader
    transactions: List[BlockTxModel] | None = None
    verboseData: VerboseDataModel
    extra: ExtraModel | None = None


class BlockResponse(BaseModel):
    blockHashes: List[str] = []
    blocks: List[BlockModel] | None = None

async def get_block_from_vecnod(block_hash: str, include_transactions: bool, include_color: bool) -> dict | None:
    """Fetch block directly from vecnod using VecnodMultiClient (correctly)"""
    payload = {
        "hash": block_hash,
        "includeTransactions": include_transactions
    }

    try:
        resp = await vecnod_client.request("getBlockRequest", payload, timeout=15)
        block = resp.get("getBlockResponse", {}).get("block")

        if not block:
            return None

        convert_to_legacy_block(block)
        _logger.debug(f"Found block in vecnod: {block_hash}")

        block["extra"] = {}
        if include_color:
            verbose = block.get("verboseData", {})
            if verbose.get("isChainBlock"):
                block["extra"]["color"] = "blue"
            else:
                color = await get_block_color_from_vecnod(block_hash)
                block["extra"]["color"] = color or "unknown"

        return block

    except Exception as e:
        _logger.warning(f"vecnod getBlock failed for {block_hash}: {e}")
        return None


async def get_block_color_from_vecnod(block_hash: str) -> str | None:
    """Get current block color from vecnod"""
    try:
        resp = await vecnod_client.request(
            "getCurrentBlockColorRequest",
            {"hash": block_hash},
            timeout=10
        )
        result = resp.get("getCurrentBlockColorResponse", {})
        if result.get("blue") is True:
            return "blue"
        if result.get("blue") is False:
            return "red"
    except Exception as e:
        _logger.warning(f"Failed to get block color for {block_hash}: {e}")
    return None


async def get_block_color_from_db(block: dict) -> str | None:
    block_hash = block["verboseData"]["hash"]
    async with async_session_blocks() as s:
        blocks = (
            await s.execute(
                select(Block)
                .distinct()
                .join(TransactionAcceptance, TransactionAcceptance.block_hash == Block.hash)
                .join(BlockParent, BlockParent.block_hash == TransactionAcceptance.block_hash)
                .filter(BlockParent.parent_hash == block_hash)
            )
        ).scalars().all()

        for b in blocks:
            if block_hash in (b.merge_set_blues_hashes or []):
                return "blue"
            if block_hash in (b.merge_set_reds_hashes or []):
                return "red"
    return None

@app.get("/blocks/{blockId}", response_model=BlockModel, tags=["Vecno blocks"])
async def get_block(
    response: Response,
    blockId: str = Path(regex="[a-f0-9]{64}"),
    includeTransactions: bool = True,
    includeColor: bool = False,
):
    """
    Get block information for a given block id
    """
    block = await get_block_from_vecnod(blockId, includeTransactions, includeColor)
    if not block and IS_SQL_DB_CONFIGURED:
        response.headers["X-Data-Source"] = "Database"
        block = await get_block_from_db(blockId, includeTransactions)
        if block:
            logging.debug(f"Found block {blockId} in database")
            if includeColor:
                if block["verboseData"]["isChainBlock"]:
                    block["extra"] = {"color": "blue"}
                else:
                    block["extra"] = {"color": await get_block_color_from_db(block)}
    if block:
        miner_payload = get_miner_payload_from_block(block)
        if miner_payload:
            miner_info, miner_address = retrieve_miner_info_from_payload(miner_payload)
            block.setdefault("extra", {})
            block["extra"]["minerInfo"] = miner_info
            block["extra"]["minerAddress"] = miner_address
        if not includeTransactions:
            block["transactions"] = None
    else:
        raise HTTPException(status_code=404, detail="Block not found", headers={"Cache-Control": "public, max-age=8"})

    add_cache_control(block.get("header", {}).get("blueScore"), block.get("header", {}).get("timestamp"), response)
    return block

@app.get("/blocks", response_model=BlockResponse, tags=["Vecno blocks"])
async def get_blocks(
    response: Response,
    lowHash: str = Query(regex="[a-f0-9]{64}"),
    includeBlocks: bool = False,
    includeTransactions: bool = False,
):
    """
    Lists blocks beginning from a low hash (block id).
    """
    response.headers["Cache-Control"] = "public, max-age=3"

    payload = {
        "lowHash": lowHash,
        "includeBlocks": includeBlocks,
        "includeTransactions": includeTransactions
    }

    try:
        resp = await vecnod_client.request("getBlocksRequest", payload, timeout=60)
        result = resp.get("getBlocksResponse", {})

        for block in result.get("blocks", []):
            convert_to_legacy_block(block)

        return result
    except Exception as e:
        _logger.warning(f"getBlocks RPC failed: {e}")
        return {"blockHashes": [], "blocks": []}


@app.get("/blocks-from-bluescore", response_model=List[BlockModel], tags=["Vecno blocks"])
async def get_blocks_from_bluescore(
    response: Response,
    blueScore: int = 43679173,
    includeTransactions: bool = False
):
    """
    Lists blocks of a given blueScore
    """
    response.headers["X-Data-Source"] = "Database"

    if blueScore < 0 or (current_blue_score_data["blue_score"] and current_blue_score_data["blue_score"] - blueScore < 0):
        return []

    add_cache_control(blueScore, None, response)

    if (current_blue_score_data["blue_score"] - blueScore) / BPS < 86400:
        async with async_session_blocks() as s:
            block_hashes = (await s.execute(select(Block.hash).where(Block.blue_score == blueScore))).scalars().all()

        if block_hashes:
            result = []
            for h in block_hashes:
                block = await get_block_from_vecnod(h, includeTransactions, False)
                if block:
                    result.append(block)
            if result:
                return result

    # Older blocks: use DB
    async with async_session_blocks() as s:
        rows = (await s.execute(block_join_query().where(Block.blue_score == blueScore))).all()

    result = []
    for block, is_chain_block, parents, children, transaction_ids in rows:
        transactions = None
        if includeTransactions and transaction_ids:
            transactions = await get_transactions(block.hash, transaction_ids)
        result.append(
            map_block_from_db(block, is_chain_block, parents, children, transaction_ids, transactions)
        )
    return result

async def get_block_from_db(block_hash: str, include_transactions: bool) -> dict | None:
    async with async_session_blocks() as s:
        row = (await s.execute(block_join_query().where(Block.hash == block_hash).limit(1))).first()

    if row:
        block, is_chain_block, parents, children, transaction_ids = row
        transactions = await get_transactions(block.hash, transaction_ids) if include_transactions and transaction_ids else None
        return map_block_from_db(block, is_chain_block, parents, children, transaction_ids, transactions)

    return None


def block_join_query():
    return select(
        Block,
        exists().where(TransactionAcceptance.block_hash == Block.hash).label("is_chain_block"),
        select(func.array_agg(BlockParent.parent_hash))
        .where(BlockParent.block_hash == Block.hash)
        .scalar_subquery(),
        select(func.array_agg(BlockParent.block_hash))
        .where(BlockParent.parent_hash == Block.hash)
        .scalar_subquery(),
        select(func.array_agg(BlockTransaction.transaction_id))
        .where(BlockTransaction.block_hash == Block.hash)
        .scalar_subquery(),
    )


async def get_transactions(block_hash: str, transaction_ids: List[str]):
    if not transaction_ids:
        return []

    async with async_session() as s:
        txs = (
            await s.execute(
                select(Transaction, Subnetwork)
                .join(Subnetwork, Transaction.subnetwork_id == Subnetwork.id)
                .where(Transaction.transaction_id.in_(transaction_ids))
                .order_by(Subnetwork.id)
            )
        ).all()

        outputs = (
            await s.execute(
                select(TransactionOutput)
                .where(TransactionOutput.transaction_id.in_(transaction_ids))
                .order_by(TransactionOutput.transaction_id, TransactionOutput.index)
            )
        ).scalars().all()

        inputs = (
            await s.execute(
                select(TransactionInput)
                .where(TransactionInput.transaction_id.in_(transaction_ids))
                .order_by(TransactionInput.transaction_id, TransactionInput.index)
            )
        ).scalars().all()

    tx_map = {}
    for tx, sub in txs:
        tx_map[tx.transaction_id] = {
            "inputs": [],
            "outputs": [],
            "subnetworkId": sub.subnetwork_id,
            "payload": tx.payload,
            "verboseData": {
                "transactionId": tx.transaction_id,
                "hash": tx.hash,
                "computeMass": tx.mass,
                "blockHash": block_hash,
                "blockTime": tx.block_time,
            },
            "mass": tx.mass,
            "version": 0,
        }

    for inp in inputs:
        if inp.transaction_id in tx_map:
            tx_map[inp.transaction_id]["inputs"].append({
                "previousOutpoint": {
                    "transactionId": inp.previous_outpoint_hash,
                    "index": inp.previous_outpoint_index,
                },
                "signatureScript": inp.signature_script,
                "sigOpCount": inp.sig_op_count,
            })

    for out in outputs:
        if out.transaction_id in tx_map:
            tx_map[out.transaction_id]["outputs"].append({
                "amount": out.amount,
                "scriptPublicKey": {
                    "scriptPublicKey": out.script_public_key,
                    "version": 0
                },
                "verboseData": {
                    "scriptPublicKeyType": out.script_public_key_type,
                    "scriptPublicKeyAddress": out.script_public_key_address,
                }
            })

    return list(tx_map.values())


def map_block_from_db(block, is_chain_block, parents, children, transaction_ids, transactions):
    return {
        "header": {
            "version": block.version,
            "hashMerkleRoot": block.hash_merkle_root,
            "acceptedIdMerkleRoot": block.accepted_id_merkle_root,
            "utxoCommitment": block.utxo_commitment,
            "timestamp": str(block.timestamp),
            "bits": block.bits,
            "nonce": block.nonce,
            "daaScore": str(block.daa_score),
            "blueWork": block.blue_work or "0",
            "parents": [{"parentHashes": parents or []}],
            "blueScore": str(block.blue_score),
            "pruningPoint": block.pruning_point,
        },
        "transactions": transactions or [],
        "verboseData": {
            "hash": block.hash,
            "difficulty": bits_to_difficulty(block.bits) if block.bits else None,
            "selectedParentHash": block.selected_parent_hash,
            "transactionIds": transaction_ids or [],
            "blueScore": str(block.blue_score),
            "childrenHashes": children or [],
            "mergeSetBluesHashes": block.merge_set_blues_hashes or [],
            "mergeSetRedsHashes": block.merge_set_reds_hashes or [],
            "isChainBlock": bool(is_chain_block),
        },
        "extra": {}
    }


def convert_to_legacy_block(block: dict) -> dict:
    header = block.get("header", {})
    if "blueWork" in header and header["blueWork"]:
        header["blueWork"] = header["blueWork"].lstrip("0") or "0"

    parents = []
    for level in header.get("parentsByLevel", []):
        parents.append({"parentHashes": level})
    header["parents"] = parents

    for tx in block.get("transactions", []):
        for out in tx.get("outputs", []):
            out["amount"] = out.get("value")
            spk = out.get("scriptPublicKey", "")
            if spk:
                spk = spk.lstrip("0")
                if len(spk) % 2 == 1:
                    spk = "0" + spk
                out["scriptPublicKey"] = {"scriptPublicKey": spk, "version": 0}

            verbose = out.get("verboseData", {})
            if "scriptPublicKeyType" in verbose:
                verbose["scriptPublicKeyType"] = verbose["scriptPublicKeyType"].lower()

    return block