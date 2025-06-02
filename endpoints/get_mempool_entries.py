# encoding: utf-8
from typing import List, Dict
from fastapi import HTTPException
from pydantic import BaseModel

from server import app, vecnod_client


class vecnodMempoolEntriesResponse(BaseModel):
    entries: List[Dict] = []
    error: str = ""


@app.get("/info/mempoolentries", response_model=vecnodMempoolEntriesResponse, tags=["Vecno Network info"])
async def get_mempool_entries(
        IncludeOrphanPool: bool = False,
        FilterTransactionPool: bool = False):
    """
    Get  current mempool entries for vecno network.
    """
    resp = await vecnod_client.request("getMempoolEntriesRequest", 
                                    params={
                                      "IncludeOrphanPool": IncludeOrphanPool,
                                      "FilterTransactionPool": FilterTransactionPool
                                    })
    
    if resp["getMempoolEntriesResponse"].get('error'):
        raise HTTPException(400, detail=resp.get('error').get('message'))
    return resp
