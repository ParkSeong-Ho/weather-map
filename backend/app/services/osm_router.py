"""
OSM 기반 날씨 가중치 라우팅.
각 도로 엣지에 uv_cost / night_cost / rain_cost 가중치를 적용해
날씨 조건별로 실제로 다른 경로를 계산한다.
"""
import math
import logging
from pathlib import Path
import networkx as nx
import osmnx as ox

logger = logging.getLogger(__name__)


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


GRAPH_PATH = Path(__file__).parent.parent.parent / "data" / "daejeon_weather_graph.graphml"
_G = None


def _build_graph():
    """그래프 파일이 없을 때 OSM에서 다운로드해 빌드한다 (첫 실행 1회)."""
    CENTER = (36.3504, 127.3845)
    DIST_M = 3000
    logger.info("OSM 그래프 빌드 시작 (약 30~60초 소요)...")
    GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
    G = ox.graph_from_point(CENTER, dist=DIST_M, network_type="walk", retain_all=False)
    for u, v, k, data in G.edges(keys=True, data=True):
        highway = data.get("highway", "residential")
        if isinstance(highway, list):
            highway = highway[0]
        lit     = data.get("lit", "no")
        covered = data.get("covered", "no")
        tunnel  = data.get("tunnel", "no")
        if covered == "yes" or tunnel == "yes":
            data["uv_cost"] = 0.0
        elif highway in ("footway", "pedestrian"):
            data["uv_cost"] = 0.3
        elif highway in ("primary", "secondary"):
            data["uv_cost"] = 0.9
        elif highway in ("residential", "living_street"):
            data["uv_cost"] = 0.5
        else:
            data["uv_cost"] = 0.6
        if lit == "yes":
            data["night_cost"] = 0.1
        elif highway in ("primary", "secondary", "tertiary"):
            data["night_cost"] = 0.2
        elif highway in ("footway", "path", "steps"):
            data["night_cost"] = 0.9
        else:
            data["night_cost"] = 0.5
        if covered == "yes" or tunnel == "yes":
            data["rain_cost"] = 0.0
        elif highway == "pedestrian":
            data["rain_cost"] = 0.3
        elif highway in ("footway", "path"):
            data["rain_cost"] = 0.7
        else:
            data["rain_cost"] = 0.85
    ox.save_graphml(G, GRAPH_PATH)
    logger.info("OSM 그래프 빌드 완료: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())


def _load_graph():
    global _G
    if _G is None:
        if not GRAPH_PATH.exists():
            logger.warning("그래프 파일 없음 → 자동 빌드 시작 (배포 후 최초 1회)")
            _build_graph()
        _G = ox.load_graphml(GRAPH_PATH)
        logger.info("OSM 그래프 로드 완료: %d nodes, %d edges", _G.number_of_nodes(), _G.number_of_edges())
    return _G


def _polyline_distance_m(polyline: list) -> float:
    total = 0.0
    for i in range(len(polyline) - 1):
        a, b = polyline[i], polyline[i + 1]
        dlat = math.radians(b["lat"] - a["lat"])
        dlng = math.radians(b["lng"] - a["lng"])
        h = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(a["lat"]))
             * math.cos(math.radians(b["lat"]))
             * math.sin(dlng / 2) ** 2)
        total += 6_371_000 * 2 * math.asin(math.sqrt(h))
    return total


def compute_weather_route(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    context_tags: list,
    scores: dict,
) -> tuple:
    """
    날씨 가중치를 적용한 최적 보행 경로를 계산한다.

    - 자외선_높음/매우높음: uv_cost 높은 도로(대로, 야외) 회피
    - 야간: night_cost 높은 도로(조명 없음, 소로) 회피
    - 비/눈: rain_cost 높은 도로(야외 보행로) 회피

    Returns:
        (polyline, distance_m) — 성공 시
        ([], None)             — 실패 시 (폴백 사용)
    """
    try:
        G = _load_graph()

        start_node = ox.nearest_nodes(G, start_lng, start_lat)
        end_node   = ox.nearest_nodes(G, end_lng, end_lat)

        # 입력 좌표가 그래프 경계 밖이면 폴백 사용 (nearest_nodes가 엉뚱한 노드 반환)
        MAX_SNAP_M = 500
        sn = G.nodes[start_node]
        en = G.nodes[end_node]
        if (_haversine_m(start_lat, start_lng, sn["y"], sn["x"]) > MAX_SNAP_M or
                _haversine_m(end_lat, end_lng, en["y"], en["x"]) > MAX_SNAP_M):
            logger.info("입력 좌표가 OSM 그래프 범위 밖 → 폴백 사용")
            return [], None

        # scores를 0~1 범위로 정규화
        shade_w  = min(scores.get("shade", 0) / 70.0, 1.0)
        safety_w = min(scores.get("safety", 0) / 100.0, 1.0)

        def weight_fn(u, v, data):
            length  = float(data.get("length", 1.0))
            penalty = 1.0

            if "자외선_높음" in context_tags:
                penalty += float(data.get("uv_cost", 0.5)) * shade_w * 3.0
            if "자외선_매우높음" in context_tags:
                penalty += float(data.get("uv_cost", 0.5)) * shade_w * 5.0
            if "야간" in context_tags:
                penalty += float(data.get("night_cost", 0.5)) * safety_w * 4.0
            if "비" in context_tags or "눈" in context_tags:
                penalty += float(data.get("rain_cost", 0.8)) * safety_w * 3.0

            return length * penalty

        path_nodes = nx.shortest_path(G, start_node, end_node, weight=weight_fn)

        polyline = [
            {"lat": G.nodes[n]["y"], "lng": G.nodes[n]["x"]}
            for n in path_nodes
        ]
        distance = _polyline_distance_m(polyline)
        return polyline, distance

    except Exception as e:
        logger.warning("OSM 라우팅 실패 (%s), 폴백 사용", e)
        return [], None
