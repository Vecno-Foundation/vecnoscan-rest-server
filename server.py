# encoding: utf-8
import logging
import os

import fastapi.logger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi_utils.tasks import repeat_every
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from helper.LimitUploadSize import LimitUploadSize
from vecnod.VecnodMultiClient import VecnodMultiClient

fastapi.logger.logger.setLevel(logging.WARNING)

app = FastAPI(
    title="Vecno REST-API server",
    description="This server is to communicate with Vecno Network via REST-API",
    version=os.getenv("VERSION", "tbd"),
    contact={
        "name": "lAmeR1"
    },
    license_info={
        "name": "MIT LICENSE"
    }
)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(LimitUploadSize, max_upload_size=200_000)  # ~1MB

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Data-Source", "X-Page-Count", "X-Next-Page-After", "X-Next-Page-Before"],
)


class PingResponse(BaseModel):
    serverVersion: str = "0.0.1"
    isUtxoIndexed: bool = True
    isSynced: bool = True


@app.get("/ping",
         include_in_schema=False,
         response_model=PingResponse)
async def ping_server():
    """
    Ping Pong
    """
    try:
        info = await vecnod_client.vecnods[0].request("getInfoRequest")
        assert info["getInfoResponse"]["isSynced"] is True

        return {
            "server_version": info["getInfoResponse"]["serverVersion"],
            "is_utxo_indexed": info["getInfoResponse"]["isUtxoIndexed"],
            "is_synced": info["getInfoResponse"]["isSynced"]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail="vecnod not connected.")


VECNOD_HOSTS = []

for i in range(100):
    try:
        VECNOD_HOSTS.append(os.environ[f"VECNOD_HOSTS_{i + 1}"].strip())
    except KeyError:
        break

if not VECNOD_HOSTS:
    raise Exception('Please set at least VECNOD_HOSTS_1 environment variable.')

vecnod_client = VecnodMultiClient(VECNOD_HOSTS)


@app.exception_handler(Exception)
async def unicorn_exception_handler(request: Request, exc: Exception):
    await vecnod_client.initialize_all()
    return JSONResponse(
        status_code=500,
        content={"message": "Internal server error"
                 # "traceback": f"{traceback.format_exception(exc)}"
                 },
    )


@app.on_event("startup")
@repeat_every(seconds=60)
async def periodical_blockdag():
    await vecnod_client.initialize_all()
