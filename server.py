# encoding: utf-8
import logging
import os
import asyncio
import csv

import fastapi.logger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi_utils.tasks import repeat_every
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from helper.LimitUploadSize import LimitUploadSize
from vecnod.VecnodMultiClient import VecnodMultiClient
from sqlalchemy.future import select
from models.Balance import Balance
from dbsession import async_session
# =====================================

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
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        content={"message": "Internal server error"},
    )


@app.on_event("startup")
@repeat_every(seconds=60)
async def periodical_blockdag():
    await vecnod_client.initialize_all()

STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
TOP_1000_CSV_PATH = os.path.join(STATIC_DIR, "top-1000.csv")

async def generate_top_1000_csv():
    """Generate fresh top-1000.csv file atomically"""
    async with async_session() as session:
        result = await session.execute(
            select(Balance)
            .order_by(Balance.balance.desc())
            .limit(1000)
        )
        balances = result.scalars().all()

        temp_path = TOP_1000_CSV_PATH + ".tmp"
        with open(temp_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Address", "Balance"])
            for b in balances:
                writer.writerow([b.script_public_key_address, b.balance])

        os.replace(temp_path, TOP_1000_CSV_PATH)
        print(f"top-1000.csv updated – {len(balances)} addresses – {int(asyncio.get_event_loop().time())}")

async def top_1000_updater_task():
    await asyncio.sleep(10)
    while True:
        try:
            await generate_top_1000_csv()
        except Exception as e:
            print("Failed to update top-1000.csv:", e)
        await asyncio.sleep(120)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.on_event("startup")
async def start_top_1000_updater():
    asyncio.create_task(top_1000_updater_task())
