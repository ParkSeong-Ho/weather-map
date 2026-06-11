import httpx
import math
from app.config import KAKAO_REST_API_KEY, TMAP_APP_KEY
from app.services.osm_router import (
    compute_weather_route,
    compute_shortest_walk,
    trim_pedestrian_polyline,
)

# 정적 경유지 (야간 전용 + 폴백)
CONTEXT_WAYPOINTS = {
    "야간": [
        {"lat": 36.3284, "lng": 127.4282, "label": "으능정이 문화의거리", "type": "lit_road"},
        {"lat": 36.3277, "lng": 127.4273, "label": "성심당 본점 일대", "type": "lit_road"},
    ],
    "비": [
        {"lat": 36.3519, "lng": 127.3782, "label": "갤러리아 타임월드", "type": "shelter"},
        {"lat": 36.3271, "lng": 127.4215, "label": "중앙로 지하상가", "type": "shelter"},
    ],
    "눈": [
        {"lat": 36.3519, "lng": 127.3782, "label": "갤러리아 타임월드", "type": "shelter"},
        {"lat": 36.3271, "lng": 127.4215, "label": "중앙로 지하상가", "type": "shelter"},
    ],
    "자외선_높음": [
        {"lat": 36.3689, "lng": 127.3894, "label": "한밭수목원", "type": "shade"},
    ],
    "자외선_매우높음": [
        {"lat": 36.3271, "lng": 127.4215, "label": "중앙로 지하상가", "type": "shelter"},
    ],
    "주간": [
        {"lat": 36.3041, "lng": 127.4168, "label": "보문산공원", "type": "park"},
        {"lat": 36.3689, "lng": 127.3894, "label": "한밭수목원", "type": "park"},
    ],
}


def get_waypoints_for_tags(context_tags: list) -> list:
    waypoints = []
    for tag in context_tags:
        if tag in CONTEXT_WAYPOINTS:
            waypoints.extend(CONTEXT_WAYPOINTS[tag])
    return waypoints


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# 보행자가 통과할 수 없는 차량 전용 시설 (경유지 후보에서 제외)
_IMPASSABLE_KEYWORDS = ("지하차도", "주차장", "고가차도", "터널", "톨게이트", "IC", "램프", "나들목", "분기점")

METRIC_LABELS = {
    "shade_pct":   "그늘 비율",
    "lit_pct":     "밝은 구간",
    "covered_pct": "차폐 구간",
}


def _is_walkable_place(name: str) -> bool:
    return not any(kw in name for kw in _IMPASSABLE_KEYWORDS)


async def _search_place_near(lat: float, lng: float, keyword: str, radius: int) -> dict | None:
    """카카오 로컬 API로 주변 장소 검색. 차량 전용 시설은 제외."""
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "query": keyword,
        "x": str(lng),
        "y": str(lat),
        "radius": radius,
        "size": 5,
        "sort": "distance",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            docs = resp.json().get("documents", [])
        for doc in docs:
            if _is_walkable_place(doc["place_name"]):
                return {
                    "lat": float(doc["y"]),
                    "lng": float(doc["x"]),
                    "label": doc["place_name"],
                    "type": "dynamic",
                }
    except Exception:
        pass
    return None


async def _get_context_waypoint(
    tags: list,
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    scores: dict = None,
) -> list:
    """
    가중치(scores)를 기반으로 경유지 전략 결정.

    shade >= 40 → 공원/수목원 경유 (UV 높음/매우높음 + 더위)
    safety  + 야간 → 정적 밝은 거리 경유
    비/눈 → 경유지 없이 기본 경로 + 경고 메시지만 (지하상가 경유는 비실용적)
    """
    s = scores or {}
    shade_score = s.get("shade", 0)

    mid_lat = (start_lat + end_lat) / 2
    mid_lng = (start_lng + end_lng) / 2
    dist_m = _haversine_m(start_lat, start_lng, end_lat, end_lng)
    radius = int(min(max(dist_m / 2, 1000), 3000))

    # 자외선 높음 이상 (shade 점수 40+) → 가로수길(도로 위) 우선, 공원 입구 폴백
    if shade_score >= 40:
        wp = await _search_place_near(mid_lat, mid_lng, "가로수길", radius)
        if not wp:
            wp = await _search_place_near(mid_lat, mid_lng, "공원 입구", radius)
        return [wp] if wp else CONTEXT_WAYPOINTS.get("자외선_높음", [])[:1]

    # 야간 → 밝은 거리 정적 경유지
    if "야간" in tags:
        return CONTEXT_WAYPOINTS["야간"][:1]

    # 비 → 지하도/지하상가 동적 검색 (실내 경유 안전 경로)
    if "비" in tags:
        wp = await _search_place_near(mid_lat, mid_lng, "지하도", radius)
        if not wp:
            wp = await _search_place_near(mid_lat, mid_lng, "지하상가", radius)
        return [wp] if wp else []

    # 눈 → 지하도/지하상가 동적 검색 (비와 동일 전략)
    if "눈" in tags:
        wp = await _search_place_near(mid_lat, mid_lng, "지하도", radius)
        if not wp:
            wp = await _search_place_near(mid_lat, mid_lng, "지하상가", radius)
        return [wp] if wp else []

    # 주간(맑음) → 가로수길 경유 산책 경로, 실패 시 공원 입구 → 정적 공원 폴백
    if "주간" in tags:
        wp = await _search_place_near(mid_lat, mid_lng, "가로수길", radius)
        if not wp:
            wp = await _search_place_near(mid_lat, mid_lng, "공원 입구", radius)
        return [wp] if wp else CONTEXT_WAYPOINTS.get("주간", [])[:1]

    return []


async def _fetch_tmap_foot_route(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
) -> tuple:
    """T맵 보행자 경로 API. (polyline, distance_m) 반환. 실패 시 ([], None)."""
    url = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1"
    headers = {"appKey": TMAP_APP_KEY, "Content-Type": "application/json"}
    body = {
        "startX": str(start_lng), "startY": str(start_lat),
        "endX":   str(end_lng),   "endY":   str(end_lat),
        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
        "startName": "출발지", "endName": "목적지",
        "searchOption": "0",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            data = resp.json()
        features = data.get("features", [])
        polyline, distance = [], None
        for f in features:
            geom = f.get("geometry", {})
            props = f.get("properties", {})
            if distance is None and props.get("totalDistance"):
                distance = props["totalDistance"]
            if geom.get("type") == "LineString":
                for coord in geom["coordinates"]:
                    polyline.append({"lat": coord[1], "lng": coord[0]})
        return polyline, distance
    except Exception:
        return [], None


async def _fetch_kakao_route_full(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    waypoints: list = None,
    priority: str = "RECOMMEND",
) -> tuple:
    """카카오 경로 API 호출, (polyline, duration_sec, distance_m) 반환. 실패 시 ([], None, None)."""
    url = "https://apis-navi.kakaomobility.com/v1/directions"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "origin": f"{start_lng},{start_lat}",
        "destination": f"{end_lng},{end_lat}",
        "priority": priority,
    }
    if waypoints:
        params["waypoints"] = "|".join(
            [f"{wp['lng']},{wp['lat']}" for wp in waypoints[:1]]
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            data = resp.json()

        routes = data.get("routes", [])
        if not routes or routes[0].get("result_code") != 0:
            return [], None, None

        summary = routes[0].get("summary", {})
        duration = summary.get("duration")   # 초 단위
        distance = summary.get("distance")   # 미터 단위

        polyline = []
        for section in routes[0]["sections"]:
            for road in section["roads"]:
                vx = road["vertexes"]
                for i in range(0, len(vx) - 1, 2):
                    polyline.append({"lat": vx[i + 1], "lng": vx[i]})
        return polyline, duration, distance
    except Exception:
        return [], None, None


async def fetch_kakao_route(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    waypoints: list = None,
    priority: str = "RECOMMEND",
) -> list:
    url = "https://apis-navi.kakaomobility.com/v1/directions"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "origin": f"{start_lng},{start_lat}",
        "destination": f"{end_lng},{end_lat}",
        "priority": priority,
    }
    if waypoints:
        params["waypoints"] = "|".join(
            [f"{wp['lng']},{wp['lat']}" for wp in waypoints[:1]]
        )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            data = resp.json()

        routes = data.get("routes", [])
        if not routes or routes[0].get("result_code") != 0:
            return []

        # vertexes: [lng, lat, lng, lat, ...]
        polyline = []
        for section in routes[0]["sections"]:
            for road in section["roads"]:
                vx = road["vertexes"]
                for i in range(0, len(vx) - 1, 2):
                    polyline.append({"lat": vx[i + 1], "lng": vx[i]})
        return polyline
    except Exception:
        return []


async def build_routes(
    context_tags: list,
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    scores: dict = None,
) -> dict:
    context_wps = await _get_context_waypoint(
        context_tags, start_lat, start_lng, end_lat, end_lng, scores
    )

    # 최단 경로 (자동차 시간 기준)
    normal_polyline, normal_duration, normal_distance = await _fetch_kakao_route_full(
        start_lat, start_lng, end_lat, end_lng, priority="TIME"
    )

    # 도보/자전거용 폴리라인: T맵 보행자 경로 우선
    # → 실패 시 OSM 최단 보행 (차 경로 폴백이 블록을 빙 도는 문제 방지)
    # → 최후 폴백 Kakao RECOMMEND
    foot_polyline, normal_foot_distance = await _fetch_tmap_foot_route(
        start_lat, start_lng, end_lat, end_lng
    )
    if foot_polyline:
        # 도착지를 지나쳐 되돌아오는 꼬리 제거 (보행자는 일방통행 제약 없음)
        foot_polyline = trim_pedestrian_polyline(
            foot_polyline, start_lat, start_lng, end_lat, end_lng
        )
    else:
        foot_polyline, foot_dist = compute_shortest_walk(
            start_lat, start_lng, end_lat, end_lng
        )
        if foot_polyline:
            normal_foot_distance = round(foot_dist)
    if not foot_polyline:
        foot_polyline, _, normal_foot_distance = await _fetch_kakao_route_full(
            start_lat, start_lng, end_lat, end_lng, priority="RECOMMEND"
        )

    # 날씨 최적 경로: OSM 가중치 라우팅 우선 → 실패 시 Kakao waypoint 폴백
    used_wps = []  # 실제로 경로에 반영된 경유지만 기록 (라벨 정확성)
    context_polyline, context_distance, context_stats = compute_weather_route(
        start_lat, start_lng, end_lat, end_lng,
        context_tags, scores or {}
    )
    if context_polyline:
        # 도보 기준 소요시간 (4km/h ≈ 67m/min)
        context_duration = int(context_distance / 67) if context_distance else None
    else:
        # 폴백 1: Kakao waypoint 경로
        if context_wps:
            context_polyline, context_duration, context_distance = await _fetch_kakao_route_full(
                start_lat, start_lng, end_lat, end_lng,
                waypoints=context_wps, priority="RECOMMEND",
            )
            if context_polyline:
                used_wps = context_wps[:1]
        # 폴백 2: 경유지 없는 RECOMMEND
        if not context_polyline:
            context_polyline, context_duration, context_distance = await _fetch_kakao_route_full(
                start_lat, start_lng, end_lat, end_lng, priority="RECOMMEND",
            )
        # 폴백 3: normal 경로 재활용
        if not context_polyline:
            context_polyline = normal_polyline
            context_duration = normal_duration
            context_distance = normal_distance

    route_option = "bigroad" if "야간" in context_tags else "normal"

    wp_label = used_wps[0]["label"] if used_wps else ""
    s = scores or {}

    via = f" — {wp_label} 경유" if wp_label else ""

    # OSM 경로 통계 → 최단 경로 대비 환경 개선을 수치로 표기
    w_stat = (context_stats or {}).get("weather") or {}
    b_stat = (context_stats or {}).get("baseline") or {}

    def _vs(metric: str) -> str:
        w, b = w_stat.get(metric), b_stat.get(metric)
        if w is None:
            return ""
        if b is not None and w > b:
            return f" — {METRIC_LABELS[metric]} {w}% (최단 경로 {b}%)"
        return f" — {METRIC_LABELS[metric]} {w}%"

    if s.get("shade", 0) >= 40:
        w, b = w_stat.get("shade_pct"), b_stat.get("shade_pct")
        if w is not None and b is not None and w - b < 8:
            # 실데이터상 그늘 우회로가 사실상 없는 방향 — 과장 없이 알림
            context_desc = f"이 방향은 그늘이 적습니다 (그늘 {w}% · 최단 {b}%) — 모자·선크림 필수"
        else:
            context_desc = f"자외선 회피 그늘 우선 경로{via or _vs('shade_pct')}"
    elif "야간" in context_tags:
        context_desc = f"야간 밝은 거리 우선 경로{via or _vs('lit_pct')}"
    elif "비" in context_tags:
        context_desc = f"비 조건 안전 경로{via or _vs('covered_pct')}" if not wp_label else f"비 조건 실내 경유 안전 경로{via}"
    elif "눈" in context_tags:
        context_desc = f"눈 조건 안전 경로{via or _vs('covered_pct')}" if not wp_label else f"눈 조건 실내 경유 안전 경로{via}"
    elif "주간" in context_tags:
        context_desc = f"산책로 권장 경로{via}"
    else:
        context_desc = "날씨 최적 경로"

    return {
        "normal": {
            "type": "normal",
            "description": "자동차 최단 시간 경로 (카카오 내비)",
            "waypoints": [],
            "route_option": "normal",
            "polyline": normal_polyline,             # 자동차용 (TIME priority)
            "foot_polyline": foot_polyline or [],    # 도보/자전거용 (RECOMMEND priority)
            "duration": normal_duration,
            "distance": normal_distance,
            "foot_distance": normal_foot_distance,
        },
        "context": {
            "type": "context",
            "description": context_desc,
            "waypoints": used_wps,
            "weather_stats": context_stats,
            "route_option": route_option,
            "context_tags": context_tags,
            "polyline": context_polyline,
            "duration": context_duration,
            "distance": context_distance,
            "foot_distance": context_distance,  # RECOMMEND 계열이라 그대로 사용
        },
    }
