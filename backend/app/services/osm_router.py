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


def _edge_between(u: str, v: str) -> dict | None:
    return next((e for e in _adj.get(u, []) if e["to"] == v), None)


def _path_coords(path_nodes: list) -> list:
    """노드 경로 → 도로 곡선 geometry를 포함한 (lat, lng) 좌표열."""
    coords = []
    for i, n in enumerate(path_nodes):
        if i > 0:
            edge = _edge_between(path_nodes[i - 1], n)
            if edge and "geom" in edge:
                g = edge["geom"]
                coords.extend((g[j], g[j + 1]) for j in range(0, len(g), 2))
        nd = _nodes[n]
        coords.append((nd["lat"], nd["lng"]))
    return coords


def _project_on_segment(plat, plng, alat, alng, blat, blng) -> tuple:
    """점 P를 선분 AB에 투영. (투영점 lat, 투영점 lng, P까지 거리 m) 반환."""
    kx = 111_320 * math.cos(math.radians(plat))
    ky = 110_540
    ax, ay = (alng - plng) * kx, (alat - plat) * ky
    bx, by = (blng - plng) * kx, (blat - plat) * ky
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    qx, qy = ax + t * dx, ay + t * dy
    return alat + t * (blat - alat), alng + t * (blng - alng), math.hypot(qx, qy)


def _snap_endpoint(coords: list, lat: float, lng: float, at_start: bool, window_m: float = 300) -> list:
    """
    경로의 시작/끝을 입력 좌표의 수선 발에서 자른다.
    가장 가까운 노드가 진행 방향 반대편에 있을 때 생기는 '튀어나온' 스파이크 제거.
    """
    if len(coords) < 2:
        return coords
    seq = coords if at_start else list(reversed(coords))
    best = None  # (dist, seg_idx, qlat, qlng)
    walked = 0.0
    for i in range(len(seq) - 1):
        a, b = seq[i], seq[i + 1]
        qlat, qlng, dist = _project_on_segment(lat, lng, a[0], a[1], b[0], b[1])
        if best is None or dist < best[0]:
            best = (dist, i, qlat, qlng)
        walked += _haversine_m(a[0], a[1], b[0], b[1])
        if walked > window_m:
            break
    _, i, qlat, qlng = best
    trimmed = [(qlat, qlng)] + list(seq[i + 1:])
    return trimmed if at_start else list(reversed(trimmed))


def _build_polyline(coords: list, start_lat, start_lng, end_lat, end_lng) -> list:
    """
    양끝 꼬리를 입력 좌표의 수선 발에서 잘라낸 도로 위 폴리라인 생성.
    입력 좌표 자체는 포함하지 않는다 — 핀까지의 연결은 프론트가 점선으로 그린다.
    """
    coords = _snap_endpoint(coords, start_lat, start_lng, at_start=True)
    coords = _snap_endpoint(coords, end_lat, end_lng, at_start=False)
    return [{"lat": la, "lng": ln} for la, ln in coords]


def trim_pedestrian_polyline(
    polyline: list,
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
) -> list:
    """
    외부 API(T맵 등) 보행자 폴리라인의 꼬리 제거.
    도착지를 지나쳐 되돌아오는 구간을 수선 발에서 잘라낸다 —
    보행자는 일방통행 제약이 없어 안전. 자동차 경로에는 쓰지 말 것.
    """
    if len(polyline) < 2:
        return polyline
    coords = [(p["lat"], p["lng"]) for p in polyline]
    return _build_polyline(coords, start_lat, start_lng, end_lat, end_lng)


def compute_shortest_walk(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
) -> tuple:
    """순수 최단 보행 경로 (날씨 가중치 없음). (polyline, distance_m), 실패 시 ([], None)."""
    try:
        _load_graph()
        na, da = _nearest_node(start_lat, start_lng)
        nb, db = _nearest_node(end_lat, end_lng)
        if da > 500 or db > 500 or na == nb:
            return [], None
        nodes = _dijkstra(na, nb, lambda e: e["length"])
        if not nodes:
            return [], None
        polyline = _build_polyline(
            _path_coords(nodes), start_lat, start_lng, end_lat, end_lng
        )
        return polyline, _polyline_distance_m(polyline)
    except Exception as e:
        logger.warning("OSM 최단 보행 경로 실패 (%s)", e)
        return [], None


def _path_stats(path_nodes: list) -> dict | None:
    """경로의 길이 가중 환경 비율 (그늘 %, 밝은 구간 %, 지붕/차폐 %)."""
    total = sun = dark = wet = 0.0
    for i in range(len(path_nodes) - 1):
        u, v = path_nodes[i], path_nodes[i + 1]
        edge = next((e for e in _adj.get(u, []) if e["to"] == v), None)
        if not edge:
            continue
        length = edge["length"]
        total += length
        sun   += length * edge["uv_cost"]
        dark  += length * edge["night_cost"]
        wet   += length * edge["rain_cost"]
    if total == 0:
        return None
    return {
        "shade_pct":   round((1 - sun / total) * 100),
        "lit_pct":     round((1 - dark / total) * 100),
        "covered_pct": round((1 - wet / total) * 100),
    }


def compute_weather_route(
    start_lat: float, start_lng: float,
    end_lat: float, end_lng: float,
    context_tags: list,
    scores: dict,
) -> tuple:
    """
    날씨 가중치를 적용한 최적 보행 경로를 계산한다.

    Returns:
        (polyline, distance_m, stats) — 성공 시.
            stats = {"weather": {...}, "baseline": {...}}
            weather는 날씨 가중 경로, baseline은 같은 그래프의 순수 최단 경로 환경 비율.
        ([], None, None)              — 실패 시 (폴백 사용)
    """
    try:
        _load_graph()

        start_node, start_snap = _nearest_node(start_lat, start_lng)
        end_node,   end_snap   = _nearest_node(end_lat, end_lng)

        MAX_SNAP_M = 500
        if start_snap > MAX_SNAP_M or end_snap > MAX_SNAP_M:
            logger.info("입력 좌표가 OSM 그래프 범위 밖 → 폴백 사용")
            return [], None, None

        shade_w  = min(scores.get("shade", 0) / 70.0, 1.0)
        safety_w = min(scores.get("safety", 0) / 100.0, 1.0)

        def weight_fn(edge):
            length  = edge["length"]
            penalty = 1.0
            if "자외선_높음" in context_tags:
                penalty += edge["uv_cost"] * shade_w * 3.5
            if "자외선_매우높음" in context_tags:
                penalty += edge["uv_cost"] * shade_w * 5.0
            # 야간 유효 가중치 k=4.0 — 이 지점부터 대로(가로등) 비중이 84%로 포화 (실측)
            if "야간" in context_tags:
                penalty += edge["night_cost"] * safety_w * 10.0
            if "비" in context_tags or "눈" in context_tags:
                penalty += edge["rain_cost"] * safety_w * 9.0
            return length * penalty

        path_nodes = _dijkstra(start_node, end_node, weight_fn)
        if not path_nodes:
            return [], None, None

        polyline = _build_polyline(
            _path_coords(path_nodes), start_lat, start_lng, end_lat, end_lng
        )
        distance = _polyline_distance_m(polyline)

        # 같은 그래프의 순수 최단 경로와 환경 비율 비교 (차별성 정량 지표)
        base_nodes = _dijkstra(start_node, end_node, lambda e: e["length"])
        stats = {
            "weather":  _path_stats(path_nodes),
            "baseline": _path_stats(base_nodes) if base_nodes else None,
        }
        return polyline, distance, stats

    except Exception as e:
        logger.warning("OSM 라우팅 실패 (%s), 폴백 사용", e)
        return [], None, None


def _leg_coords(alat: float, alng: float, blat: float, blng: float) -> list:
    """두 점 사이를 보행자 최단 경로로 연결한 (lat, lng) 좌표열. 실패 시 직선 폴백."""
    straight = [(alat, alng), (blat, blng)]
    if _haversine_m(alat, alng, blat, blng) < 10:
        return straight
    na, da = _nearest_node(alat, alng)
    nb, db = _nearest_node(blat, blng)
    MAX_SNAP_M = 500
    if da > MAX_SNAP_M or db > MAX_SNAP_M or na == nb:
        return straight
    nodes = _dijkstra(na, nb, lambda e: e["length"])
    if not nodes:
        return straight
    coords = _path_coords(nodes)
    coords = _snap_endpoint(coords, alat, alng, at_start=True)
    coords = _snap_endpoint(coords, blat, blng, at_start=False)
    return [(alat, alng)] + coords + [(blat, blng)]


def compute_custom_path(points: list) -> tuple:
    """
    사용자가 클릭한 점들을 보행자 도로에 스냅한 폴리라인으로 변환.
    인접한 점 쌍마다 최단 경로를 이어 붙이므로 시작점과 도착점이
    같은 순환 코스도 경유지를 따라 실제 길이 그려진다.

    Returns:
        (polyline, distance_m) — polyline은 [{"lat", "lng"}, ...]
    """
    try:
        _load_graph()
    except FileNotFoundError:
        coords = [(p["lat"], p["lng"]) for p in points]
        polyline = [{"lat": la, "lng": ln} for la, ln in coords]
        return polyline, _polyline_distance_m(polyline)

    coords = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        leg = _leg_coords(a["lat"], a["lng"], b["lat"], b["lng"])
        if coords:
            leg = leg[1:]  # 이전 구간 끝점과 중복 제거
        coords.extend(leg)
    polyline = [{"lat": la, "lng": ln} for la, ln in coords]
    return polyline, _polyline_distance_m(polyline)
