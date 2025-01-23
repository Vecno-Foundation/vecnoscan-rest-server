# encoding: utf-8
import logging
import os
from typing import Optional

import fastapi.logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi_utils.tasks import repeat_every
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse

from dbsession import async_session
from helper.StrictRoute import StrictRoute
from helper.LimitUploadSize import LimitUploadSize
from vecnod.VecnodMultiClient import VecnodMultiClient

fastapi.logger.logger.setLevel(logging.WARNING)

_logger = logging.getLogger(__name__)

app = FastAPI(
    title="Vecno REST-API server",
    description="This server is to communicate with vecno network via REST-API",
    version=os.getenv("VERSION") or "tbd",
    contact={"name": "Yoshiki"},
    license_info={"name": "MIT LICENSE"},
)
app.router.route_class = StrictRoute

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(LimitUploadSize, max_upload_size=200_000)  # ~1MB

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VecnodStatus(BaseModel):
    is_online: bool = False
    server_version: Optional[str] = None
    is_utxo_indexed: Optional[bool] = None
    is_synced: Optional[bool] = None


class DatabaseStatus(BaseModel):
    is_online: bool = False


class PingResponse(BaseModel):
    vecnod: VecnodStatus = VecnodStatus()
    database: DatabaseStatus = DatabaseStatus()


@app.get("/ping", include_in_schema=False, response_model=PingResponse)
async def ping_server():
    """
    Ping Pong
    """
    result = PingResponse()

    error = False
    try:
        info = await vecnod_client.vecnods[0].request("getInfoRequest")
        result.vecnod.is_online = True
        result.vecnod.server_version = info["getInfoResponse"]["serverVersion"]
        result.vecnod.is_utxo_indexed = info["getInfoResponse"]["isUtxoIndexed"]
        result.vecnod.is_synced = info["getInfoResponse"]["isSynced"]
    except Exception as err:
        _logger.error("Vecnod health check failed %s", err)
        error = True

    if os.getenv("SQL_URI") is not None:
        async with async_session() as session:
            try:
                await session.execute("SELECT 1")
                result.database.is_online = True
            except Exception as err:
                _logger.error("Database health check failed %s", err)
                error = True

    if error or not result.vecnod.is_synced:
        return JSONResponse(status_code=500, content=result.dict())

    return result


vecnod_hosts = []

for i in range(100):
    try:
        vecnod_hosts.append(os.environ[f"VECNOD_HOST_{i + 1}"].strip())
    except KeyError:
        break

if not vecnod_hosts:
    raise Exception("Please set at least VECNOD_HOST_1 environment variable.")

vecnod_client = VecnodMultiClient(vecnod_hosts)


@app.exception_handler(Exception)
async def unicorn_exception_handler(request: Request, exc: Exception):
    await vecnod_client.initialize_all()
    return JSONResponse(
        status_code=500,
        content={
            "message": "Internal server error"
            # "traceback": f"{traceback.format_exception(exc)}"
        },
    )


@app.on_event("startup")
@repeat_every(seconds=60)
async def periodical_blockdag():
    await vecnod_client.initialize_all()
