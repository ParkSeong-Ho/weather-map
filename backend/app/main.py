import asyncio
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import weather, route, auth, custom_route
from app.database import init_db
from app.config import ALLOWED_ORIGINS

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"http://localhost:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _prebuild_graph():
    try:
        from app.services.osm_router import _load_graph
        _load_graph()
    except Exception as e:
        logger.warning("OSM 그래프 사전 빌드 실패 (첫 경로 요청 시 재시도): %s", e)


@app.on_event("startup")
async def on_startup():
    init_db()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _prebuild_graph)

app.include_router(weather.router)
app.include_router(route.router)
app.include_router(auth.router)
app.include_router(custom_route.router)

@app.get("/")
def root():
    return {"message": "백엔드 서버 정상 작동 중"}