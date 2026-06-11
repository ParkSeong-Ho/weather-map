from fastapi import APIRouter
from pydantic import BaseModel, Field
from datetime import datetime
from app.services.weather_service import get_weather, calculate_sunrise_sunset
from app.services.context_engine import get_context_tags, get_context_scores, get_context_warnings
from app.services.route_engine import build_routes
from app.services.osm_router import compute_custom_path

router = APIRouter(prefix="/api/route", tags=["route"])


class _PathPoint(BaseModel):
    lat: float
    lng: float


class _SnapRequest(BaseModel):
    points: list[_PathPoint] = Field(min_length=2, max_length=50)


@router.post("/snap")
async def snap_path(body: _SnapRequest):
    """클릭한 점들을 보행자 도로에 스냅한 폴리라인 반환 (커스텀 경로용).
    순환 코스(시작점 ≈ 도착점)도 경유지를 따라 실제 길이 그려진다."""
    points = [{"lat": p.lat, "lng": p.lng} for p in body.points]
    polyline, distance = compute_custom_path(points)
    return {"polyline": polyline, "distance": round(distance)}

# 시연용 날씨 강제 상태 (터미널에서 /api/route/demo/{preset}으로 전환)
_demo_override: str | None = None
_DEMO_PRESETS = ("off", "uv_high", "uv_extreme", "rain", "snow", "night")


@router.post("/demo/{preset}")
async def set_demo(preset: str):
    """시연용 날씨 강제 설정. off로 해제."""
    global _demo_override
    if preset not in _DEMO_PRESETS:
        return {"error": f"unknown preset. 사용 가능: {_DEMO_PRESETS}"}
    _demo_override = None if preset == "off" else preset
    return {"demo": _demo_override or "off (실제 날씨)"}


@router.get("/demo")
async def get_demo():
    return {"demo": _demo_override or "off (실제 날씨)"}


@router.get("/recommend")
async def recommend_route(start_lat: float, start_lng: float, end_lat: float, end_lng: float):
    # 1. 날씨 + UV 데이터
    weather_data = await get_weather(start_lat, start_lng)
    uv_index = weather_data["uv_index"]

    # 2. 현재 시각 + 일출/일몰
    current_time = datetime.now().hour
    sunrise, sunset = calculate_sunrise_sunset(start_lat, start_lng)

    # 데모 모드: 날씨 입력값을 강제로 덮어써서 시연용 시나리오 재현
    demo = _demo_override
    if demo == "uv_high":
        uv_index = 7
        weather_data["uv_index"] = 7
        weather_data["rain_probability"] = 0
        weather_data["snow_probability"] = 0
        current_time = (sunrise + sunset) // 2  # 한낮
    elif demo == "uv_extreme":
        uv_index = 9
        weather_data["uv_index"] = 9
        weather_data["rain_probability"] = 0
        weather_data["snow_probability"] = 0
        current_time = (sunrise + sunset) // 2
    elif demo == "rain":
        weather_data["rain_probability"] = 85
        weather_data["snow_probability"] = 0
        uv_index = 1
        weather_data["uv_index"] = 1
        current_time = (sunrise + sunset) // 2  # 주간 고정 (야간 태그 섞임 방지)
    elif demo == "snow":
        weather_data["snow_probability"] = 80
        weather_data["rain_probability"] = 0
        uv_index = 1
        weather_data["uv_index"] = 1
        current_time = (sunrise + sunset) // 2
    elif demo == "night":
        current_time = 23
        weather_data["rain_probability"] = 0
        weather_data["snow_probability"] = 0
        uv_index = 0
        weather_data["uv_index"] = 0

    # 3. 상황 태그
    context_tags = get_context_tags(weather_data, uv_index, current_time, sunset, sunrise)

    # 4. 가중치 점수 + 경고 메시지
    scores = get_context_scores(context_tags, uv_index, weather_data)
    warnings = get_context_warnings(context_tags, uv_index, weather_data)

    # 5. 경로 생성 (가중치 전달)
    routes = await build_routes(context_tags, start_lat, start_lng, end_lat, end_lng, scores)

    # 6. 대표 추천 문자열 (가중치 dominant 기준)
    dominant = max(scores, key=scores.get)
    if scores[dominant] == 0:
        recommendation = "일반 경로 추천 — 날씨가 좋습니다!"
    elif dominant == "shade":
        recommendation = "그늘 경로 추천 — 자외선이 높아 공원·그늘 경유 경로를 안내합니다."
    elif dominant == "indoor":
        recommendation = "실내 이동 권장 — 자외선 또는 강수가 심합니다."
    else:  # safety
        if "눈" in context_tags:
            recommendation = "안전 경로 추천 — 눈길 미끄럼 주의 경로를 안내합니다."
        elif "야간" in context_tags:
            recommendation = "야간 경로 추천 — 밝은 거리 위주의 경로를 안내합니다."
        else:
            recommendation = "안전 경로 추천 — 비 또는 결빙 구간 주의."

    return {
        "start": {"lat": start_lat, "lng": start_lng},
        "end": {"lat": end_lat, "lng": end_lng},
        "context_tags": context_tags,
        "scores": scores,
        "warnings": warnings,
        "weather": weather_data,
        "recommendation": recommendation,
        "routes": routes,
    }
