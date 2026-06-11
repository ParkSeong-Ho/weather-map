import { useRef, useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { api } from "../../api/client";
import { fetchSnappedPath } from "../../api/route";
import { getToken } from "../../api/auth";
import MapScreenScaffold from "../../layout/MapScreenScaffold";

const DEFAULT_CENTER = { lat: 36.3504, lng: 127.3845 };

export default function CustomRouteDraw() {
  const mapRef = useRef(null);
  const mapInstance = useRef(null);
  const markersRef = useRef([]);
  const polylineRef = useRef(null);
  const pointsRef = useRef([]);
  const snapSeqRef = useRef(0);

  const [points, setPoints] = useState([]);
  const [name, setName] = useState("");
  const [isPublic, setIsPublic] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    const { kakao } = window;
    if (!kakao || !mapRef.current) return;

    const center = new kakao.maps.LatLng(DEFAULT_CENTER.lat, DEFAULT_CENTER.lng);
    mapInstance.current = new kakao.maps.Map(mapRef.current, { center, level: 6 });

    kakao.maps.event.addListener(mapInstance.current, "click", (e) => {
      const lat = e.latLng.getLat();
      const lng = e.latLng.getLng();
      const k = window.kakao;

      const marker = new k.maps.Marker({ position: e.latLng, map: mapInstance.current });
      markersRef.current.push(marker);

      const newPoints = [...pointsRef.current, { lat, lng }];
      pointsRef.current = newPoints;
      setPoints([...newPoints]);
    });
  }, []);

  const drawPolyline = (pathPoints, { dashed = false } = {}) => {
    const { kakao } = window;
    if (polylineRef.current) {
      polylineRef.current.outer.setMap(null);
      polylineRef.current.inner.setMap(null);
      polylineRef.current = null;
    }
    if (!kakao || !mapInstance.current || pathPoints.length < 2) return;
    const path = pathPoints.map((p) => new kakao.maps.LatLng(p.lat, p.lng));
    const style = dashed ? "shortdash" : "solid";
    const outerPl = new kakao.maps.Polyline({ path, strokeWeight: 10, strokeColor: "#FFFFFF", strokeOpacity: 0.9, strokeStyle: style, endArrow: true });
    outerPl.setMap(mapInstance.current);
    const innerPl = new kakao.maps.Polyline({ path, strokeWeight: 6, strokeColor: "#2C8A57", strokeOpacity: 0.9, strokeStyle: style, endArrow: true });
    innerPl.setMap(mapInstance.current);
    polylineRef.current = { outer: outerPl, inner: innerPl };
  };

  // 점이 바뀔 때마다: 직선 미리보기(점선) 즉시 → 도로 스냅 경로(실선)로 교체
  useEffect(() => {
    drawPolyline(points, { dashed: true });
    if (points.length < 2) return;
    const seq = ++snapSeqRef.current;
    fetchSnappedPath(points)
      .then((data) => {
        if (seq !== snapSeqRef.current) return; // 이후 클릭으로 무효화된 응답
        if (data.polyline?.length >= 2) drawPolyline(data.polyline);
      })
      .catch(() => {}); // 실패 시 직선 미리보기 유지
  }, [points]);

  const handleUndo = () => {
    if (pointsRef.current.length === 0) return;
    const lastMarker = markersRef.current.pop();
    if (lastMarker) lastMarker.setMap(null);
    const newPoints = pointsRef.current.slice(0, -1);
    pointsRef.current = newPoints;
    setPoints([...newPoints]);
  };

  const handleClear = () => {
    markersRef.current.forEach((m) => m.setMap(null));
    markersRef.current = [];
    pointsRef.current = [];
    setPoints([]);
  };

  const handleSave = async () => {
    if (points.length < 2) return setError("최소 2개 이상의 지점을 클릭하세요.");
    if (!name.trim()) return setError("경로 이름을 입력하세요.");
    if (!getToken()) return setError("로그인이 필요합니다.");
    setSaving(true); setError("");
    try {
      await api.post("/api/routes/custom", {
        name: name.trim(),
        start_lat: points[0].lat, start_lng: points[0].lng,
        end_lat: points[points.length - 1].lat, end_lng: points[points.length - 1].lng,
        waypoints: points.slice(1, -1),
        is_public: isPublic,
      });
      navigate("/custom");
    } catch {
      setError("저장에 실패했습니다. 다시 시도하세요.");
    } finally {
      setSaving(false);
    }
  };

  const instruction =
    points.length === 0 ? "지도를 클릭해 출발지를 설정하세요"
    : points.length === 1 ? "다음 지점을 클릭하세요 (계속 클릭해 경유지 추가)"
    : `${points.length}개 지점 설정됨 · 마지막 클릭이 도착지`;

  const panel = (
    <div className="p-5 flex flex-col gap-4">
      <h2 className="text-[17px] font-extrabold text-ink">경로 그리기</h2>

      <div className="flex items-center gap-2">
        <button onClick={handleUndo} disabled={points.length === 0}
          className="px-3 py-1.5 text-sm rounded-lvl2 border border-line text-muted hover:bg-bg disabled:opacity-40 transition">
          되돌리기
        </button>
        <button onClick={handleClear} disabled={points.length === 0}
          className="px-3 py-1.5 text-sm rounded-lvl2 border border-red-200 text-red-500 hover:bg-red-50 disabled:opacity-40 transition">
          초기화
        </button>
        <Link to="/custom" className="ml-auto text-sm text-faint hover:text-muted transition">
          저장된 경로 →
        </Link>
      </div>

      <input
        type="text"
        placeholder="경로 이름을 입력하세요"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-full border border-line rounded-lvl2 px-3 py-2.5 text-sm bg-card focus:outline-none focus:border-primary text-ink placeholder:text-faint"
      />

      <label className="flex items-center gap-2 text-sm text-muted cursor-pointer select-none">
        <input type="checkbox" checked={isPublic} onChange={(e) => setIsPublic(e.target.checked)} />
        공개 경로로 저장
      </label>

      {error && <p className="text-red-500 text-sm">{error}</p>}

      <button
        onClick={handleSave}
        disabled={saving || points.length < 2}
        className="w-full bg-cta text-white py-3 rounded-card font-bold hover:opacity-90 disabled:opacity-40 transition-all"
      >
        {saving ? "저장 중..." : "경로 저장"}
      </button>
    </div>
  );

  const map = (
    <div className="absolute top-5 left-1/2 -translate-x-1/2 pointer-events-none">
      <div className="px-4 py-2 rounded-full bg-card shadow-md border border-line text-[13px] font-semibold text-ink whitespace-nowrap">
        {instruction}
      </div>
    </div>
  );

  const sheetHeader = (
    <h1 className="m-0 text-[20px] font-extrabold text-ink tracking-[-0.02em]">경로 그리기</h1>
  );

  return <MapScreenScaffold mapRef={mapRef} panel={panel} map={map} sheetHeader={sheetHeader} />;
}
