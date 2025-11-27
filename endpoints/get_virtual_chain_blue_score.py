# encoding: utf-8
import logging
from fastapi_utils.tasks import repeat_every
from pydantic import BaseModel

from server import app, vecnod_client

_logger = logging.getLogger(__name__)

current_blue_score_data = {
    "blue_score": 0
}


class BlockdagResponse(BaseModel):
    blueScore: int = 260890


@app.get("/info/virtual-chain-blue-score", response_model=BlockdagResponse, tags=["Vecno network info"])
async def get_virtual_selected_parent_blue_score():
    """
    Returns the blue score of virtual selected parent
    """
    resp = await vecnod_client.request("getSinkBlueScoreRequest")
    return resp["getSinkBlueScoreResponse"]


@app.on_event("startup")
@repeat_every(seconds=5)
async def update_blue_score():
    global current_blue_score_data
    blue_score = await get_virtual_selected_parent_blue_score()
    current_blue_score_data["blue_score"] = int(blue_score["blueScore"])
    logging.debug(f"Updated current_blue_score: {current_blue_score_data['blue_score']}")
