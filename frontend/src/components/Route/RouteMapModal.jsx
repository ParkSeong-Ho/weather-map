import { useRef, useEffect } from "react";
import { fetchSnappedPath } from "../../api/route";

export default function RouteMapModal({ route, onClose }) {
  const mapRef = useRef(null);

  useEffect(() => {
    if (!route || !mapRef.current) return;
    const { kakao } = window;
    if (!kakao) return;

    const center = new kakao.maps.LatLng(route.start_lat, route.start_lng);
    const map = new kakao.maps.Map(mapRef.current, { center, level: 6 });

    const allPoints = [
      { lat: route.start_lat, lng: route.start_lng },
      ...(Array.isArray(route.waypoints) ? route.waypoints : []),
      { lat: route.end_lat, lng: route.end_lng },
    ];

    const path = allPoints.map((p) => new kakao.maps.LatLng(p.lat, p.lng));

    const outerPl = new kakao.maps.Polyline({
      path, strokeWeight: 10, strokeColor: "#FFFFFF",
      strokeOpacity: 0.9, strokeStyle: "solid", endArrow: true,
    });
    outerPl.setMap(map);

    const innerPl = new kakao.maps.Polyline({
      path, strokeWeight: 6, strokeColor: "#2C8A57",
      strokeOpacity: 0.9, strokeStyle: "solid", endArrow: true,
    });
    innerPl.setMap(map);

    new kakao.maps.Marker({
      position: new kakao.maps.LatLng(route.start_lat, route.start_lng),
      map,
      title: "출발",
    });
    new kakao.maps.Marker({
      position: new kakao.maps.LatLng(route.end_lat, route.end_lng),
      map,
      title: "도착",
    });

    const bounds = new kakao.maps.LatLngBounds();
    path.forEach((p) => bounds.extend(p));
    map.setBounds(bounds);

    // 직선 미리보기 → 보행자 도로 스냅 경로로 교체 (순환 코스도 실제 길 표시)
    let cancelled = false;
    fetchSnappedPath(allPoints)
      .then((data) => {
        if (cancelled || !(data.polyline?.length >= 2)) return;
        const snapped = data.polyline.map((p) => new kakao.maps.LatLng(p.lat, p.lng));
        outerPl.setPath(snapped);
        innerPl.setPath(snapped);
        const b = new kakao.maps.LatLngBounds();
        snapped.forEach((p) => b.extend(p));
        map.setBounds(b);
      })
      .catch(() => {}); // 실패 시 직선 미리보기 유지

    return () => {
      cancelled = true;
      outerPl.setMap(null);
      innerPl.setMap(null);
    };
  }, [route]);

  const waypointCount = Array.isArray(route?.waypoints) ? route.waypoints.length : 0;

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl w-[90vw] max-w-2xl flex flex-col shadow-2xl"
        style={{ height: "80vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-800">{route?.name}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-2xl leading-none transition-colors"
          >
            ×
          </button>
        </div>

        <div className="flex-1" style={{ minHeight: 0 }}>
          <div ref={mapRef} className="w-full h-full" />
        </div>

        <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">
          경유지 {waypointCount}개 · 지도에서 경로를 확인하세요
        </div>
      </div>
    </div>
  );
}
