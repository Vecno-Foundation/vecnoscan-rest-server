# endpoints/get_hashrate.py
import json
import logging
from datetime import datetime
from pydantic import BaseModel
from sqlalchemy import select, func
from constants import BPS
from dbsession import async_session_blocks
from endpoints import sql_db_only
from helper import KeyValueStore
from models.Block import Block
from helper.difficulty_calculation import bits_to_difficulty
from server import app, vecnod_client

_logger = logging.getLogger(__name__)


class BlockHeader(BaseModel):
    hash: str
    timestamp: str
    difficulty: int
    daaScore: str
    blueScore: str


class HashrateResponse(BaseModel):
    hashrate: float


class MaxHashrateResponse(BaseModel):
    hashrate: float
    blockheader: BlockHeader


class HashratePoint(BaseModel):
    timestamp: str  # ISO 8601
    blueScore: int
    hashrate: float  # in MH/s


class HashrateHistoryResponse(BaseModel):
    data: list[HashratePoint]


@app.get("/info/hashrate", response_model=HashrateResponse | str, tags=["Vecno network info"])
async def get_hashrate(stringOnly: bool = False):
    """
    Returns the current hashrate for Vecno network in Mh/s.
    """
    resp = await vecnod_client.request("getBlockDagInfoRequest")
    difficulty = resp["getBlockDagInfoResponse"]["difficulty"]
    hashrate = difficulty * 2
    hashrate_in_mh = hashrate / 1_000_000

    if stringOnly:
        return f"{hashrate_in_mh:.2f}"

    return HashrateResponse(hashrate=hashrate_in_mh)


@app.get("/info/hashrate/max", response_model=MaxHashrateResponse, tags=["Vecno network info"])
@sql_db_only
async def get_max_hashrate():
    """
    Returns the all-time highest hashrate and the block where it occurred.
    """
    global MAXHASH_CACHE

    cached_json = await KeyValueStore.get("maxhash_last_value")
    cached_bluescore = int(await KeyValueStore.get("maxhash_last_bluescore") or "0")

    cached_data = json.loads(cached_json or "{}")
    cached_hashrate = cached_data.get("hashrate", 0)

    async with async_session_blocks() as s:
        result = await s.execute(
            select(Block)
            .where(Block.blue_score > cached_bluescore)
            .order_by(Block.bits.asc())
            .limit(1)
        )
        block = result.scalar_one_or_none()

    if block:
        current_difficulty = bits_to_difficulty(block.bits)
        current_hashrate = current_difficulty * 2 * BPS / 1_000_000  # in MH/s

        if current_hashrate > cached_hashrate:
            timestamp_iso = datetime.fromtimestamp(block.timestamp / 1000).isoformat() if isinstance(block.timestamp, (int, float)) else block.timestamp.isoformat()

            response = MaxHashrateResponse(
                hashrate=current_hashrate,
                blockheader=BlockHeader(
                    hash=block.hash,
                    timestamp=timestamp_iso,
                    difficulty=int(current_difficulty),
                    daaScore=str(block.daa_score or 0),
                    blueScore=str(block.blue_score or 0),
                )
            )

            # Update cache
            await KeyValueStore.set("maxhash_last_value", response.model_dump_json())
            await KeyValueStore.set("maxhash_last_bluescore", str(block.blue_score))

            _logger.info(f"New max hashrate: {current_hashrate:,.2f} MH/s at blueScore {block.blue_score}")
            return response

    if cached_data:
        return MaxHashrateResponse(**cached_data)

    resp = await vecnod_client.request("getBlockDagInfoRequest")
    difficulty = resp["getBlockDagInfoResponse"]["difficulty"]
    hashrate_mh = difficulty * 2 / 1_000_000

    fallback = MaxHashrateResponse(
        hashrate=hashrate_mh,
        blockheader=BlockHeader(
            hash="0000000000000000000000000000000000000000000000000000000000000000",
            timestamp=datetime.utcnow().isoformat(),
            difficulty=int(difficulty),
            daaScore="0",
            blueScore="0",
        )
    )
    await KeyValueStore.set("maxhash_last_value", fallback.model_dump_json())
    return fallback


@app.get("/info/hashrate/history", response_model=HashrateHistoryResponse, tags=["Vecno network info"])
@sql_db_only
async def get_hashrate_history():
    """
    Returns hashrate history with one data point every 10 minutes for the last 7 days.
    Optimized for block explorer charts: time-aligned, efficient, and smooth.
    """
    interval_minutes = 10
    days = 7

    # Cutoff: 7 days ago in milliseconds
    cutoff_timestamp_ms = int(datetime.utcnow().timestamp() * 1000) - (days * 24 * 60 * 60 * 1000)

    async with async_session_blocks() as s:
        # Bucket timestamps into 10-minute intervals
        bucket_size_ms = interval_minutes * 60 * 1000
        bucket_expr = (Block.timestamp // bucket_size_ms) * bucket_size_ms

        # Subquery: get the latest block (highest blue_score) in each bucket
        subq = (
            select(
                bucket_expr.label("bucket_start"),
                func.max(Block.blue_score).label("max_blue_score")
            )
            .where(Block.timestamp >= cutoff_timestamp_ms)
            .group_by("bucket_start")
            .subquery()
        )

        # Main query: join to get full block details, ordered chronologically
        stmt = (
            select(Block)
            .join(
                subq,
                (Block.blue_score == subq.c.max_blue_score) &
                (Block.timestamp >= cutoff_timestamp_ms)
            )
            .order_by(Block.timestamp.asc())
        )

        result = await s.execute(stmt)
        blocks = result.scalars().all()

    data_points = []
    for block in blocks:
        difficulty = bits_to_difficulty(block.bits)
        hashrate_mh = difficulty * 2 * BPS / 1_000_000

        timestamp_iso = (
            datetime.fromtimestamp(block.timestamp / 1000).isoformat()
            if isinstance(block.timestamp, (int, float))
            else block.timestamp.isoformat()
        )

        data_points.append(
            HashratePoint(
                timestamp=timestamp_iso,
                blueScore=block.blue_score,
                hashrate=round(hashrate_mh, 2)
            )
        )

    return HashrateHistoryResponse(data=data_points)