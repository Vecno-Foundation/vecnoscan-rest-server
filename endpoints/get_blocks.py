# encoding: utf-8
import os
from typing import List

from fastapi import Query, Path, HTTPException
from fastapi import Response
from pydantic import BaseModel
from sqlalchemy import select

from dbsession import async_session
from endpoints.get_virtual_chain_blue_score import current_blue_score_data
from models.Block import Block
from models.Transaction import Transaction, TransactionOutput, TransactionInput
from server import app, vecnod_client

IS_SQL_DB_CONFIGURED = os.getenv("SQL_URI") is not None


class VerboseDataModel(BaseModel):
    hash: str = ""
    difficulty: float = 0
    selectedParentHash: str = ""
    transactionIds: List[str] | None = []
    blueScore: str = ""
    childrenHashes: List[str] | None = None
    mergeSetBluesHashes: List[str] = []
    mergeSetRedsHashes: List[str] = []
    isChainBlock: bool | None = None


class ParentHashModel(BaseModel):
    parentHashes: List[str] = []


class BlockHeader(BaseModel):
    version: int = 0
    hashMerkleRoot: str = ""
    acceptedIdMerkleRoot: str = ""
    utxoCommitment: str = ""
    timestamp: str = ""
    bits: int = 0
    nonce: str = ""
    daaScore: str = ""
    blueWork: str = ""
    parents: List[ParentHashModel]
    blueScore: str = ""
    pruningPoint: str = ""


class BlockModel(BaseModel):
    header: BlockHeader
    transactions: list | None
    verboseData: VerboseDataModel


class BlockResponse(BaseModel):
    blockHashes: List[str] = [""]
    blocks: List[BlockModel] | None


@app.get("/blocks/{blockId}", response_model=BlockModel, tags=["Vecno blocks"])
async def get_block(response: Response,
                    blockId: str = Path(regex="[a-f0-9]{64}")):
    """
    Get block information for a given block id
    """
    resp = await vecnod_client.request("getBlockRequest",
                                       params={
                                           "hash": blockId,
                                           "includeTransactions": True
                                       })
    requested_block = None

    if "block" in resp["getBlockResponse"]:
        # We found the block in vecnod. Just use it
        requested_block = resp["getBlockResponse"]["block"]
    else:
        if IS_SQL_DB_CONFIGURED:
            # Didn't find the block in vecnod. Try getting it from the DB
            response.headers["X-Data-Source"] = "Database"
            requested_block = await get_block_from_db(blockId)

    if not requested_block:
        # Still did not get the block
        print("hier")
        raise HTTPException(status_code=404, detail="Block not found", headers={
            "Cache-Control": "public, max-age=1"
        })

    # We found the block, now we guarantee it contains the transactions
    # It's possible that the block from vecnod does not contain transactions
    if 'transactions' not in requested_block or not requested_block['transactions']:
        requested_block['transactions'] = await get_block_transactions(blockId)

    if int(requested_block["header"]["blueScore"]) > current_blue_score_data["blue_score"] - 20:
        response.headers["Cache-Control"] = "public, max-age=1"

    elif int(requested_block["header"]["blueScore"]) > current_blue_score_data["blue_score"] - 60:
        response.headers["Cache-Control"] = "public, max-age=10"

    else:
        response.headers["Cache-Control"] = "public, max-age=600"

    return requested_block


@app.get("/blocks", response_model=BlockResponse, tags=["Vecno blocks"])
async def get_blocks(response: Response,
                     lowHash: str = Query(regex="[a-f0-9]{64}"),
                     includeBlocks: bool = False,
                     includeTransactions: bool = False):
    """
    Lists block beginning from a low hash (block id). Note that this function tries to determine the blocks from
    the vecnod node. If this is not possible, the database is getting queryied as backup. In this case the response
    header contains the key value pair: x-data-source: database.

    Additionally the fields in verboseData: isChainBlock, childrenHashes and transactionIds can't be filled.
    """
    response.headers["Cache-Control"] = "public, max-age=3"

    resp = await vecnod_client.request("getBlocksRequest",
                                       params={
                                           "lowHash": lowHash,
                                           "includeBlocks": includeBlocks,
                                           "includeTransactions": includeTransactions
                                       })

    return resp["getBlocksResponse"]


@app.get("/blocks-from-bluescore", response_model=List[BlockModel], tags=["Vecno blocks"])
async def get_blocks_from_bluescore(response: Response,
                                    blueScore: int = 43679173,
                                    includeTransactions: bool = False):
    """
    Lists block beginning from a low hash (block id). Note that this function is running on a vecnod and not returning
    data from database.
    """
    response.headers["X-Data-Source"] = "Database"

    if blueScore > current_blue_score_data["blue_score"] - 20:
        response.headers["Cache-Control"] = "no-store"

    blocks = await get_blocks_from_db_by_bluescore(blueScore)

    return [{
        "header": {
            "version": block.version,
            "hashMerkleRoot": block.hash_merkle_root,
            "acceptedIdMerkleRoot": block.accepted_id_merkle_root,
            "utxoCommitment": block.utxo_commitment,
            "timestamp": round(block.timestamp.timestamp() * 1000),
            "bits": block.bits,
            "nonce": block.nonce,
            "daaScore": block.daa_score,
            "blueWork": block.blue_work,
            "parents": [{"parentHashes": block.parents}],
            "blueScore": block.blue_score,
            "pruningPoint": block.pruning_point
        },
        "transactions": (txs := (await get_block_transactions(block.hash))) if includeTransactions else None,
        "verboseData": {
            "hash": block.hash,
            "difficulty": block.difficulty,
            "selectedParentHash": block.selected_parent_hash,
            "transactionIds": [tx["verboseData"]["transactionId"] for tx in txs] if includeTransactions else None,
            "blueScore": block.blue_score,
            "childrenHashes": None,
            "mergeSetBluesHashes": block.merge_set_blues_hashes,
            "mergeSetRedsHashes": block.merge_set_reds_hashes,
            "isChainBlock": None,
        }
    } for block in blocks]


async def get_blocks_from_db_by_bluescore(blue_score):
    async with async_session() as s:
        blocks = (await s.execute(select(Block)
                                  .where(Block.blue_score == blue_score))).scalars().all()

    return blocks


async def get_block_from_db(blockId):
    """
    Get the block from the database
    """
    async with async_session() as s:
        requested_block = await s.execute(select(Block)
                                          .where(Block.hash == blockId).limit(1))

        try:
            requested_block = requested_block.first()[0]  # type: Block
        except TypeError:
            raise HTTPException(status_code=404, detail="Block not found", headers={
                "Cache-Control": "public, max-age=3"
            })

    if requested_block:
        return {
            "header": {
                "version": requested_block.version,
                "hashMerkleRoot": requested_block.hash_merkle_root,
                "acceptedIdMerkleRoot": requested_block.accepted_id_merkle_root,
                "utxoCommitment": requested_block.utxo_commitment,
                "timestamp": round(requested_block.timestamp.timestamp() * 1000),
                "bits": requested_block.bits,
                "nonce": requested_block.nonce,
                "daaScore": requested_block.daa_score,
                "blueWork": requested_block.blue_work,
                "parents": [{"parentHashes": requested_block.parents}],
                "blueScore": requested_block.blue_score,
                "pruningPoint": requested_block.pruning_point
            },
            "transactions": None,  # This will be filled later
            "verboseData": {
                "hash": requested_block.hash,
                "difficulty": requested_block.difficulty,
                "selectedParentHash": requested_block.selected_parent_hash,
                "transactionIds": None,  # information not in database
                "blueScore": requested_block.blue_score,
                "childrenHashes": None,  # information not in database
                "mergeSetBluesHashes": requested_block.merge_set_blues_hashes,
                "mergeSetRedsHashes": requested_block.merge_set_reds_hashes,
                "isChainBlock": None,  # information not in database
            }
        }
    return None


"""
Get the transactions associated with a block
"""


async def get_block_transactions(blockId):
    # create tx data
    tx_list = []

    async with async_session() as s:
        transactions = await s.execute(select(Transaction).filter(Transaction.block_hash.contains([blockId])))

        transactions = transactions.scalars().all()

        tx_outputs = await s.execute(select(TransactionOutput)
                                     .where(TransactionOutput.transaction_id
                                            .in_([tx.transaction_id for tx in transactions])))

        tx_outputs = tx_outputs.scalars().all()

        tx_inputs = await s.execute(select(TransactionInput)
                                    .where(TransactionInput.transaction_id
                                           .in_([tx.transaction_id for tx in transactions])))

        tx_inputs = tx_inputs.scalars().all()

    for tx in transactions:
        tx_list.append({
            "inputs": [
                {
                    "previousOutpoint": {
                        "transactionId": tx_inp.previous_outpoint_hash,
                        "index": tx_inp.previous_outpoint_index
                    },
                    "signatureScript": tx_inp.signature_script,
                    "sigOpCount": tx_inp.sig_op_count
                }
                for tx_inp in tx_inputs if tx_inp.transaction_id == tx.transaction_id],
            "outputs": [
                {
                    "amount": tx_out.amount,
                    "scriptPublicKey": {
                        "scriptPublicKey": tx_out.script_public_key
                    },
                    "verboseData": {
                        "scriptPublicKeyType": tx_out.script_public_key_type,
                        "scriptPublicKeyAddress": tx_out.script_public_key_address
                    }
                } for tx_out in tx_outputs if tx_out.transaction_id == tx.transaction_id],
            "subnetworkId": tx.subnetwork_id,
            "verboseData": {
                "transactionId": tx.transaction_id,
                "hash": tx.hash,
                "mass": tx.mass,
                "blockHash": tx.block_hash,
                "blockTime": tx.block_time
            }
        })

    return tx_list
