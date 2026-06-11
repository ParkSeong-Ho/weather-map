"""
대전 도심 보행자 도로 날씨 가중치 그래프 빌드 스크립트.
한 번만 실행하면 됨. 결과물: backend/data/daejeon_weather_graph.graphml
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import osmnx as ox
from shapely.ops import unary_union
from shapely.strtree import STRtree

# 대전 도심 중심 (대전역 인근) 3km 반경 보행자 네트워크
CENTER = (36.3504, 127.3845)
DIST_M = 5000

print(f"OSM에서 대전 보행자 그래프 다운로드 중 (반경 {DIST_M}m)...")
G = ox.graph_from_point(CENTER, dist=DIST_M, network_type="walk", retain_all=False)
print(f"  원본: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# ── 실제 그늘 지물 (가로수길·나무·공원·숲) ────────────────────────────
# 도로 종류 추정이 아닌 실데이터: OSM natural=tree_row/tree, leisure=park, landuse=forest
TREE_ROW_BUFFER_M = 18   # 가로수길 수관 폭 (양옆 보도 포함)
TREE_BUFFER_M     = 12   # 단일 나무 수관
PARK_BUFFER_M     = 10   # 공원 경계에 붙은 보도까지 포함

print("가로수·공원 데이터 다운로드 중...")
_, edges_gdf = ox.graph_to_gdfs(G)
crs_m = edges_gdf.estimate_utm_crs()
edges_m = edges_gdf.to_crs(crs_m)

shade_geoms = []

def _collect_shade(tags, kinds):
    try:
        feats = ox.features_from_point(CENTER, tags, dist=DIST_M + 200).to_crs(crs_m)
    except Exception as e:
        print(f"  경고: {tags} 조회 실패 ({e}) — 해당 지물 없이 진행")
        return
    for geom_types, buf in kinds:
        sel = feats[feats.geom_type.isin(geom_types)]
        shade_geoms.extend(g.buffer(buf) for g in sel.geometry if g is not None)

_collect_shade(
    {"natural": ["tree_row", "tree"]},
    [(["LineString", "MultiLineString"], TREE_ROW_BUFFER_M), (["Point"], TREE_BUFFER_M)],
)
_collect_shade(
    {"leisure": "park", "landuse": "forest"},
    [(["Polygon", "MultiPolygon"], PARK_BUFFER_M)],
)
print(f"  그늘 지물 {len(shade_geoms)}개 (가로수길·나무·공원·숲)")

_shade_tree = STRtree(shade_geoms) if shade_geoms else None

def _shaded_fraction(geom) -> float:
    """엣지 길이 중 가로수·공원 수관에 덮인 비율 (0~1)."""
    if _shade_tree is None or geom is None or geom.length == 0:
        return 0.0
    idxs = _shade_tree.query(geom)
    if len(idxs) == 0:
        return 0.0
    cover = unary_union([shade_geoms[i] for i in idxs])
    return min(geom.intersection(cover).length / geom.length, 1.0)

print("엣지별 그늘 비율 계산 중...")
frac_by_edge = {idx: _shaded_fraction(geom) for idx, geom in edges_m.geometry.items()}
shaded_cnt = sum(1 for f in frac_by_edge.values() if f > 0.3)
print(f"  그늘 30%+ 엣지: {shaded_cnt}/{len(frac_by_edge)}")

# ── 하천변 노출 (천변 도로 = 건물·나무 그늘 없는 완전 노출) ──────────────
RIVER_BUFFER_M = 25

print("하천 데이터 다운로드 중...")
river_geoms = []
try:
    water = ox.features_from_point(
        CENTER, {"natural": "water", "waterway": ["river", "stream"]}, dist=DIST_M + 200
    ).to_crs(crs_m)
    for g in water.geometry:
        if g is None:
            continue
        if g.geom_type in ("Polygon", "MultiPolygon"):
            river_geoms.append(g.buffer(RIVER_BUFFER_M))
        elif g.geom_type in ("LineString", "MultiLineString"):
            river_geoms.append(g.buffer(RIVER_BUFFER_M))
except Exception as e:
    print(f"  경고: 하천 조회 실패 ({e}) — 하천변 노출 보정 없이 진행")

_river_tree = STRtree(river_geoms) if river_geoms else None

def _river_fraction(geom) -> float:
    if _river_tree is None or geom is None or geom.length == 0:
        return 0.0
    idxs = _river_tree.query(geom)
    if len(idxs) == 0:
        return 0.0
    cover = unary_union([river_geoms[i] for i in idxs])
    return min(geom.intersection(cover).length / geom.length, 1.0)

river_by_edge = {idx: _river_fraction(geom) for idx, geom in edges_m.geometry.items()}
river_cnt = sum(1 for f in river_by_edge.values() if f > 0.5)
print(f"  하천변(50%+) 엣지: {river_cnt}/{len(river_by_edge)}")

# ── 보안등 실데이터 (대전 5개 구 공공데이터 CSV — data.go.kr 표준 스키마) ──
# 골목 야간 점수를 도로 등급 추정이 아닌 실제 보안등 설치 위치로 계산
import csv
from shapely.geometry import Point
from pyproj import Transformer

LAMP_BUFFER_M = 25   # 보안등 1기의 조명 반경

print("보안등 공공데이터 로드 중...")
_tf = Transformer.from_crs("EPSG:4326", crs_m, always_xy=True)
_cx, _cy = _tf.transform(CENTER[1], CENTER[0])
lamp_geoms = []
for f in sorted((Path(__file__).parent.parent / "data").glob("대전광역시_*보안등*.csv")):
    n_file = 0
    with open(f, encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            try:
                lat, lng = float(row["위도"]), float(row["경도"])
            except (KeyError, ValueError, TypeError):
                continue
            x, y = _tf.transform(lng, lat)
            if (x - _cx) ** 2 + (y - _cy) ** 2 > (DIST_M + 200) ** 2:
                continue
            lamp_geoms.append(Point(x, y).buffer(LAMP_BUFFER_M))
            n_file += 1
    print(f"  {f.name}: 반경 내 {n_file}기")

_lamp_tree = STRtree(lamp_geoms) if lamp_geoms else None

def _lamp_fraction(geom) -> float:
    """엣지 길이 중 보안등 조명 반경에 덮인 비율 (0~1)."""
    if _lamp_tree is None or geom is None or geom.length == 0:
        return 0.0
    idxs = _lamp_tree.query(geom)
    if len(idxs) == 0:
        return 0.0
    cover = unary_union([lamp_geoms[i] for i in idxs])
    return min(geom.intersection(cover).length / geom.length, 1.0)

lamp_by_edge = {idx: _lamp_fraction(geom) for idx, geom in edges_m.geometry.items()}
lamp_cnt = sum(1 for v in lamp_by_edge.values() if v > 0.3)
print(f"  보안등 총 {len(lamp_geoms)}기 / 조명 30%+ 엣지: {lamp_cnt}/{len(lamp_by_edge)}")

# 각 엣지에 날씨 비용 속성 추가
for u, v, k, data in G.edges(keys=True, data=True):
    highway = data.get("highway", "residential")
    if isinstance(highway, list):
        highway = highway[0]

    lit     = data.get("lit", "no")
    covered = data.get("covered", "no")
    tunnel  = data.get("tunnel", "no")

    # 차폐 인정은 보행 가능한 구조물만 — 차도형 지하차도(터널)는 보행자가
    # 통행할 수 없는데 'UV 0'으로 잡히면 경로가 그쪽으로 빨려간다
    walkable_covered = covered == "yes" or (
        tunnel == "yes" and highway in ("footway", "path", "pedestrian", "steps", "corridor")
    )

    # ── UV 비용 (0=완전차단, 1=완전노출) ──────────────────────────
    # 도로 종류는 기본 노출도만 결정 — footway도 나무가 없으면 노출
    # (하천변 산책로가 '그늘'로 오분류되던 문제 수정)
    if walkable_covered:
        data["uv_cost"] = 0.0   # 보행 지하도/아케이드 = UV 없음
    else:
        base = {
            "trunk": 0.95, "primary": 0.9, "secondary": 0.9, "tertiary": 0.85,
            "residential": 0.55, "living_street": 0.5,
            "footway": 0.75, "pedestrian": 0.75, "path": 0.7, "steps": 0.7,
        }.get(highway, 0.7)
        # 하천변 도로는 건물 그늘도 없는 완전 노출 (가로수가 실측되면 아래에서 회복)
        if river_by_edge.get((u, v, k), 0.0) > 0.5:
            base = max(base, 0.9)
        # 실측 그늘 반영: 수관에 덮인 구간 비율만큼 0.15(그늘 아래)로 보간
        frac = frac_by_edge.get((u, v, k), 0.0)
        data["uv_cost"] = round(base * (1 - frac) + 0.15 * frac, 3)

    # ── 야간 비용 (0=밝음, 1=어두움) ─────────────────────────────
    # 간선도로는 가로등 전제(0.2 유지), 골목은 보안등 실데이터로 산출
    if lit == "yes":
        data["night_cost"] = 0.1
    elif highway in ("primary", "secondary", "tertiary"):
        data["night_cost"] = 0.2   # 간선 = 도로조명 설치 대상
    else:
        dark = 0.9 if highway in ("footway", "path", "steps") else 0.6
        lamp = lamp_by_edge.get((u, v, k), 0.0)
        # 보안등 조명에 덮인 비율만큼 0.15(등 아래)로 보간
        data["night_cost"] = round(dark * (1 - lamp) + 0.15 * lamp, 3)

    # ── 비/눈 비용 (0=실내, 1=완전야외) ─────────────────────────
    if walkable_covered:
        data["rain_cost"] = 0.0
    elif highway == "pedestrian":
        data["rain_cost"] = 0.3   # 보행자 전용도로 (일부 차양)
    elif highway in ("footway", "path"):
        data["rain_cost"] = 0.7
    else:
        data["rain_cost"] = 0.85

# ── 보행 지하 코리도 주입 (OSM에 없는 지하상가 — 수동 데이터) ──────────────
# 지상 도로 선형을 따라 지하 노드를 복제하고 출입구로 연결한다.
# 비/자외선 0 (실내), 야간 0.1 (조명) — 비 경로가 실제로 지하상가를 경유하게 됨.
import json
import math
import networkx as nx

CORRIDORS = Path(__file__).parent.parent / "data" / "covered_corridors.json"

def _hav_m(y1, x1, y2, x2):
    R = 6_371_000
    p1, p2 = math.radians(y1), math.radians(y2)
    dp = math.radians(y2 - y1)
    dl = math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))

def _nearest_graph_node(lat, lng):
    return min(G.nodes, key=lambda n: _hav_m(lat, lng, G.nodes[n]["y"], G.nodes[n]["x"]))

if CORRIDORS.exists():
    spec = json.loads(CORRIDORS.read_text())
    next_id = max(int(n) for n in G.nodes) + 1
    for cor in spec.get("corridors", []):
        (alat, alng), (blat, blng) = cor["anchors"]
        na = _nearest_graph_node(alat, alng)
        nb = _nearest_graph_node(blat, blng)
        try:
            surf = nx.shortest_path(G, na, nb, weight="length")
        except nx.NetworkXNoPath:
            print(f"  경고: {cor['name']} 지상 선형 탐색 실패 — 건너뜀")
            continue
        attrs = dict(uv_cost=0.0, night_cost=0.1, rain_cost=0.0, highway="corridor")
        ids = []
        for n in surf:
            G.add_node(next_id, x=G.nodes[n]["x"], y=G.nodes[n]["y"])
            ids.append(next_id)
            next_id += 1
        total = 0.0
        for i in range(len(ids) - 1):
            u_, v_ = ids[i], ids[i + 1]
            L = _hav_m(G.nodes[u_]["y"], G.nodes[u_]["x"], G.nodes[v_]["y"], G.nodes[v_]["x"])
            total += L
            G.add_edge(u_, v_, length=L, **attrs)
            G.add_edge(v_, u_, length=L, **attrs)
        # 출입구: 양끝 + connect_every_m 간격마다 지상 노드와 연결
        step = cor.get("connect_every_m", 150)
        walked, last = 0.0, -1e9
        for i, n in enumerate(surf):
            if i > 0:
                p, q = surf[i - 1], n
                walked += _hav_m(G.nodes[p]["y"], G.nodes[p]["x"], G.nodes[q]["y"], G.nodes[q]["x"])
            if i == 0 or i == len(surf) - 1 or walked - last >= step:
                last = walked
                G.add_edge(ids[i], n, length=8.0, **attrs)
                G.add_edge(n, ids[i], length=8.0, **attrs)
        print(f"  코리도 주입: {cor['name']} — 노드 {len(ids)}개, 길이 {total:.0f}m")

out = Path(__file__).parent.parent / "data" / "daejeon_weather_graph.graphml"
ox.save_graphml(G, out)
print(f"저장 완료: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges → {out}")
