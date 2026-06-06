"""
graphml → 경량 JSON 변환 (배포용 전처리 스크립트).
osmnx/NetworkX 없이 라우팅하기 위한 최소 데이터만 추출한다.
"""
import json
from pathlib import Path

GRAPHML = Path(__file__).parent.parent / "data" / "daejeon_weather_graph.graphml"
OUTPUT = Path(__file__).parent.parent / "data" / "daejeon_graph.json"


def main():
    import osmnx as ox

    print(f"Loading {GRAPHML} ...")
    G = ox.load_graphml(GRAPHML)
    print(f"  nodes={G.number_of_nodes()}, edges={G.number_of_edges()}")

    nodes = {}
    for n, data in G.nodes(data=True):
        nodes[str(n)] = {"lat": float(data["y"]), "lng": float(data["x"])}

    adj = {}
    for u, v, data in G.edges(data=True):
        su, sv = str(u), str(v)
        entry = {
            "to": sv,
            "length": float(data.get("length", 1.0)),
            "uv_cost": float(data.get("uv_cost", 0.6)),
            "night_cost": float(data.get("night_cost", 0.5)),
            "rain_cost": float(data.get("rain_cost", 0.85)),
        }
        adj.setdefault(su, []).append(entry)

    out = {"nodes": nodes, "adj": adj}
    with open(OUTPUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    size = OUTPUT.stat().st_size / 1024 / 1024
    print(f"Saved → {OUTPUT}  ({size:.1f} MB, {len(nodes)} nodes)")


if __name__ == "__main__":
    main()
