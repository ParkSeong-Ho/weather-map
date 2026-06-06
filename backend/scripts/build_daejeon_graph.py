"""
대전 도심 보행자 도로 날씨 가중치 그래프 빌드 스크립트.
한 번만 실행하면 됨. 결과물: backend/data/daejeon_weather_graph.graphml
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import osmnx as ox

# 대전 도심 중심 (대전역 인근) 3km 반경 보행자 네트워크
CENTER = (36.3504, 127.3845)
DIST_M = 5000

print(f"OSM에서 대전 보행자 그래프 다운로드 중 (반경 {DIST_M}m)...")
G = ox.graph_from_point(CENTER, dist=DIST_M, network_type="walk", retain_all=False)
print(f"  원본: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# 각 엣지에 날씨 비용 속성 추가
for u, v, k, data in G.edges(keys=True, data=True):
    highway = data.get("highway", "residential")
    if isinstance(highway, list):
        highway = highway[0]

    lit     = data.get("lit", "no")
    covered = data.get("covered", "no")
    tunnel  = data.get("tunnel", "no")

    # ── UV 비용 (0=완전차단, 1=완전노출) ──────────────────────────
    if covered == "yes" or tunnel == "yes":
        data["uv_cost"] = 0.0   # 실내/지하 = UV 없음
    elif highway in ("footway", "pedestrian"):
        data["uv_cost"] = 0.3   # 건물 사이 좁은 보행로
    elif highway in ("primary", "secondary"):
        data["uv_cost"] = 0.9   # 대로 = 햇빛 완전 노출
    elif highway in ("residential", "living_street"):
        data["uv_cost"] = 0.5   # 주택가
    else:
        data["uv_cost"] = 0.6

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
    if covered == "yes" or tunnel == "yes":
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
