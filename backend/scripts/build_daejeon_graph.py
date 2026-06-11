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
        # 실측 그늘 반영: 수관에 덮인 구간 비율만큼 0.15(그늘 아래)로 보간
        frac = frac_by_edge.get((u, v, k), 0.0)
        data["uv_cost"] = round(base * (1 - frac) + 0.15 * frac, 3)

    # ── 야간 비용 (0=밝음, 1=어두움) ─────────────────────────────
    if lit == "yes":
        data["night_cost"] = 0.1
    elif highway in ("primary", "secondary", "tertiary"):
        data["night_cost"] = 0.2   # 주요 도로 = 가로등 많음
    elif highway in ("footway", "path", "steps"):
        data["night_cost"] = 0.9   # 소로 = 어두울 가능성
    else:
        data["night_cost"] = 0.5

    # ── 비/눈 비용 (0=실내, 1=완전야외) ─────────────────────────
    if walkable_covered:
        data["rain_cost"] = 0.0
    elif highway == "pedestrian":
        data["rain_cost"] = 0.3   # 보행자 전용도로 (일부 차양)
    elif highway in ("footway", "path"):
        data["rain_cost"] = 0.7
    else:
        data["rain_cost"] = 0.85

out = Path(__file__).parent.parent / "data" / "daejeon_weather_graph.graphml"
ox.save_graphml(G, out)
print(f"저장 완료: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges → {out}")
