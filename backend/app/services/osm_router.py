"""
OSM 기반 날씨 가중치 라우팅.
osmnx/NetworkX 없이 경량 JSON 그래프로 동작 (메모리 절약).
"""
import json
import math
import heapq
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

GRAPH_JSON = Path(__file__).parent.parent.parent / "data" / "daejeon_graph.json"

_nodes = None   # {node_id: {"lat": float, "lng": float}}
_adj   = None   # {node_id: [{"to": str, "length": float, "uv_cost": float, ...}]}


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _load_graph():
    global _nodes, _adj
    if _nodes is not None:
        return
    if not GRAPH_JSON.exists():
        raise FileNotFoundError(f"그래프 파일 없음: {GRAPH_JSON}")
    with open(GRAPH_JSON) as f:
        data = json.load(f)
    _nodes = data["nodes"]
    _adj   = data["adj"]
    logger.info("OSM 그래프 로드 완료: %d nodes", len(_nodes))


def _nearest_node(lat: float, lng: float) -> str:
    best_id, best_d = None, float("inf")
    for nid, nd in _nodes.items():
        d = _haversine_m(lat, lng, nd["lat"], nd["lng"])
        if d < best_d:
            best_d, best_id = d, nid
    return best_id, best_d


def _dijkstra(start: str, end: str, weight_fn) -> list:
    dist = {start: 0.0}
    prev = {}
    heap = [(0.0, start)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        if u == end:
            break
        for edge in _adj.get(u, []):
            v = edge["to"]
            nd = d + weight_fn(edge)
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    if end not in prev and end != start:
        return []

    path, cur = [], end
    while cur in prev:
        path.append(cur)
        cur = prev[cur]
    path.append(start)
    path.reverse()
    return path


def _polyline_distance_m(polyline: list) -> float:
    total = 0.0
    for i in range(len(polyline) - 1):
        a, b = polyline[i], polyline[i + 1]
        total += _haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
    return total


def compute_weather_route(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    context_tags: list,
    scores: dict,
) -> tuple:
    """
    날씨 가중치를 적용한 최적 보행 경로를 계산한다.

    Returns:
        (polyline, distance_m) — 성공 시
        ([], None)             — 실패 시 (폴백 사용)
    """
    try:
        _load_graph()

        start_node, start_snap = _nearest_node(start_lat, start_lng)
        end_node,   end_snap   = _nearest_node(end_lat, end_lng)

        MAX_SNAP_M = 500
        if start_snap > MAX_SNAP_M or end_snap > MAX_SNAP_M:
            logger.info("입력 좌표가 OSM 그래프 범위 밖 → 폴백 사용")
            return [], None

        shade_w  = min(scores.get("shade", 0) / 70.0, 1.0)
        safety_w = min(scores.get("safety", 0) / 100.0, 1.0)

        def weight_fn(edge):
            length  = edge["length"]
            penalty = 1.0
            if "자외선_높음" in context_tags:
                penalty += edge["uv_cost"] * shade_w * 3.0
            if "자외선_매우높음" in context_tags:
                penalty += edge["uv_cost"] * shade_w * 5.0
            if "야간" in context_tags:
                penalty += edge["night_cost"] * safety_w * 4.0
            if "비" in context_tags or "눈" in context_tags:
                penalty += edge["rain_cost"] * safety_w * 3.0
            return length * penalty

        path_nodes = _dijkstra(start_node, end_node, weight_fn)
        if not path_nodes:
            return [], None

        polyline = [
            {"lat": _nodes[n]["lat"], "lng": _nodes[n]["lng"]}
            for n in path_nodes
        ]
        distance = _polyline_distance_m(polyline)
        return polyline, distance

    except Exception as e:
        logger.warning("OSM 라우팅 실패 (%s), 폴백 사용", e)
        return [], None
