# encoding: utf-8
from asyncio import wait_for
from fastapi import HTTPException
from typing import List

from server import app, vecnod_client
from pydantic import BaseModel


class FeeEstimateBucket(BaseModel):
    feerate: int = 1
    estimatedSeconds: float = 0.004


class FeeEstimateResponse(BaseModel):
    priorityBucket: FeeEstimateBucket
    normalBuckets: List[FeeEstimateBucket]
    lowBuckets: List[FeeEstimateBucket]


@app.get("/info/fee-estimate", response_model=FeeEstimateResponse, tags=["Vecno network info"])
async def get_fee_estimate():
    """
    Get fee estimate from Vecnod.

    For all buckets, feerate values represent fee/mass of a transaction in `veni/gram` units.<br>
    Given a feerate value recommendation, calculate the required fee by
    taking the transaction mass and multiplying it by feerate: `fee = feerate * mass(tx)`
    """
    resp = await vecnod_client.request("getFeeEstimateRequest")
    if resp.get("error"):
        raise HTTPException(500, resp["error"]["message"])

    fee_estimate = resp["getFeeEstimateResponse"]

    return fee_estimate["estimate"]