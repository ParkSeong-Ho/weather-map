import { useRef, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchRouteRecommend } from "../../api/route";
import MapScreenScaffold from "../../layout/MapScreenScaffold";
import { PanelHead } from "../common/Panel";
import RouteCard from "./RouteCard";
import { Spinner, Dots, SkeletonRouteCard, StateView } from "../common/feedback";
import { IconClock, IconSun, IconStar, IconWifiOff, IconMapOff, IconChevR } from "../common/icons";

// ── 유틸 (기존 로직 100% 유지) ──────────────────────────────────────────────
function calcTravelTime(route, mode) {
  if (!route) return null;
  const footDist = route.foot_distance ?? route.distance;
  if (mode === "walk") return footDist != null ? Math.ceil(footDist / 67)  : null;
  if (mode === "bike") return footDist != null ? Math.ceil(footDist / 250) : null;
  if (mode === "car")  return route.duration  != null ? Math.ceil(route.duration / 60) : null;
  return null;
}

function formatDistance(route, mode) {
  const m = (mode === "car" ? route?.distance : route?.foot_distance) ?? route?.distance;
  if (m == null) return null;
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${m}m`;
}

function getActivePolyline(routes, key, mode) {
  const route = routes?.[key];
  if (!route) return null;
  if (key === "context") return route.polyline;
  if (mode === "car") return route.polyline;
  return route.foot_polyline?.length ? route.foot_polyline : route.polyline;
}

// ── 색상 (핸드오프 토큰 hex — Kakao API는 Tailwind 클래스 불가) ──────────────
const ROUTE_COLORS = {
  normal:  "#2542C8",
  context: "#D6831C",
};

const DEFAULT_CENTER = { lat: 36.3504, lng: 127.3845 };

// ── 소형 UI 헬퍼 컴포넌트 ──────────────────────────────────────────────────
function SegMode({ mode, setMode }) {
  const opts = [["walk", "도보"], ["bike", "자전거"], ["car", "자동차"]];
  return (
    <div className="grid grid-cols-3 gap-1 p-1 bg-chip rounded-[14px] mb-4">
      {opts.map(([k, label]) => (
        <button key={k} onClick={() => setMode(k)}
          className={`py-2.5 rounded-[11px] text-[13.5px] ${mode === k ? "bg-card shadow-sm text-ink font-bold" : "text-muted font-medium"}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

function ODPill({ origin, dest }) {
  return (
    <div className="bg-bg rounded-[14px] border border-line px-3.5 mb-4">
      <div className="flex items-center gap-3 py-[11px]">
        <span className="w-[11px] h-[11px] rounded-full bg-primary shrink-0" />
        <span className="flex-1 text-[14.5px] font-semibold text-ink truncate">{origin}</span>
        <IconChevR size={17} className="text-faint shrink-0" />
      </div>
      <div className="h-px bg-line ml-[22px]" />
      <div className="flex items-center gap-3 py-[11px]">
        <span className="w-[11px] h-[11px] rounded-[3px] bg-[#E5484D] shrink-0" />
        <span className="flex-1 text-[14.5px] font-semibold text-ink truncate">{dest}</span>
        <IconChevR size={17} className="text-faint shrink-0" />
      </div>
    </div>
  );
}

function RecommendationBanner({ weather, recommendation, contextTags }) {
  if (!recommendation) return null;
  return (
    <div className="flex gap-3 p-3.5 rounded-2xl bg-weather/10 border border-weather/30 mb-4">
      <div className="w-[38px] h-[38px] rounded-xl bg-card grid place-items-center shrink-0">
        <IconSun size={21} className="text-weather" />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-bold text-ink mb-0.5 truncate">{recommendation}</div>
        {weather && (
          <div className="text-[12.5px] text-muted leading-snug">
            {weather.temperature != null ? `${weather.temperature}°C` : "—"}
            {" · "}UV {weather.uv_index ?? "—"}
            {" · "}강수 {weather.rain_probability ?? 0}%
            {contextTags?.length > 0 && ` · ${contextTags.join(" ")}`}
          </div>
        )}
      </div>
    </div>
  );
}

function Legend() {
  return (
    <div className="bg-card rounded-xl px-3 py-2.5 shadow-sm border border-line flex flex-col gap-1.5">
      {[["bg-primary", "최단"], ["bg-weather", "날씨 최적"]].map(([c, l]) => (
        <div key={l} className="flex items-center gap-2">
          <span className={`w-5 h-1 rounded-full ${c}`} />
          <span className="text-xs font-semibold text-muted">{l}</span>
        </div>
      ))}
    </div>
  );
}

// ── 메인 컴포넌트 ───────────────────────────────────────────────────────────
export default function RouteCompare() {
  const mapRef = useRef(null);
  const mapInstance = useRef(null);
  const polylinesRef = useRef({});
  const startMarkerRef = useRef(null);
  const endOverlayRef = useRef(null);
  const pickStepRef = useRef(0);

  const [startPos, setStartPos] = useState(null);
  const [endPos, setEndPos] = useState(null);
  const [routes, setRoutes] = useState(null);
  const [recommendation, setRecommendation] = useState("");
  const [contextTags, setContextTags] = useState([]);
  const [warnings, setWarnings] = useState([]);
  const [weather, setWeather] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle|loading|ready|empty|error
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [mode, setMode] = useState("car");

  const navigate = useNavigate();

  // 카카오 지도 초기화
  useEffect(() => {
    const { kakao } = window;
    if (!kakao || !mapRef.current) return;

    const center = new kakao.maps.LatLng(DEFAULT_CENTER.lat, DEFAULT_CENTER.lng);
    mapInstance.current = new kakao.maps.Map(mapRef.current, { center, level: 7 });

    kakao.maps.event.addListener(mapInstance.current, "click", (mouseEvent) => {
      if (pickStepRef.current >= 2) return;
      const lat = mouseEvent.latLng.getLat();
      const lng = mouseEvent.latLng.getLng();
      const k = window.kakao;

      if (pickStepRef.current === 0) {
        if (startMarkerRef.current) startMarkerRef.current.setMap(null);
        startMarkerRef.current = new k.maps.Marker({ position: mouseEvent.latLng, map: mapInstance.current });
        setStartPos({ lat, lng });
        pickStepRef.current = 1;
      } else {
        // 도착 마커: Kakao CustomOverlay 빨간 핀
        if (endOverlayRef.current) endOverlayRef.current.setMap(null);
        const content = `
          <div style="position:relative;transform:translateX(-50%);font-family:Pretendard,sans-serif">
            <div style="background:#E5484D;color:#fff;font-size:12px;font-weight:700;letter-spacing:.5px;
                        padding:3px 9px;border-radius:7px;white-space:nowrap;
                        position:absolute;left:50%;top:-10px;transform:translate(-50%,-100%)">도착
              <span style="position:absolute;left:50%;bottom:-5px;transform:translateX(-50%);
                           border:5px solid transparent;border-top-color:#E5484D;border-bottom:0"></span>
            </div>
            <svg width="26" height="34" viewBox="-13 -38 26 38">
              <path d="M0 0 C-7-12-11-17-11-27a11 11 0 1 1 22 0C11-17 7-12 0 0Z"
                    fill="#E5484D" stroke="#fff" stroke-width="2.5"/>
              <circle cx="0" cy="-27" r="4.5" fill="#fff"/>
            </svg>
          </div>`;
        endOverlayRef.current = new k.maps.CustomOverlay({ position: mouseEvent.latLng, content, yAnchor: 1, map: mapInstance.current });
        setEndPos({ lat, lng });
        pickStepRef.current = 2;
      }
    });
  }, []);

  // 경로 요청
  useEffect(() => {
    if (!startPos || !endPos) return;
    const loadRoutes = async () => {
      setPhase("loading");
      setRoutes(null);
      try {
        const data = await fetchRouteRecommend(startPos.lat, startPos.lng, endPos.lat, endPos.lng);
        setRoutes(data.routes);
        setRecommendation(data.recommendation);
        setContextTags(data.context_tags || []);
        setWarnings(data.warnings || []);
        setWeather(data.weather || null);
        const hasAny = data.routes && Object.keys(data.routes).length > 0;
        setPhase(hasAny ? "ready" : "empty");
      } catch {
        setPhase("error");
      }
    };
    loadRoutes();
  }, [startPos, endPos]);

  // Polyline 그리기
  useEffect(() => {
    const { kakao } = window;
    if (!routes || !mapInstance.current || !kakao) return;

    Object.values(polylinesRef.current).forEach(({ outer, inner }) => { outer?.setMap(null); inner?.setMap(null); });
    polylinesRef.current = {};

    const bounds = new kakao.maps.LatLngBounds();
    let hasPolyline = false;
    const pathMap = {};

    Object.keys(ROUTE_COLORS).forEach((key) => {
      const polylineData = getActivePolyline(routes, key, mode);
      if (!polylineData?.length) return;
      const path = polylineData.map((p) => new kakao.maps.LatLng(p.lat, p.lng));
      path.forEach((p) => bounds.extend(p));
      pathMap[key] = path;
      hasPolyline = true;
    });

    Object.entries(ROUTE_COLORS).forEach(([key, color]) => {
      const path = pathMap[key];
      if (!path) return;
      const isSelected = selectedRoute === key;
      const outerPl = new kakao.maps.Polyline({ path, strokeWeight: isSelected ? 16 : 10, strokeColor: "#FFFFFF", strokeOpacity: isSelected ? 1.0 : 0.85, strokeStyle: "solid" });
      outerPl.setMap(mapInstance.current);
      const innerPl = new kakao.maps.Polyline({ path, strokeWeight: isSelected ? 10 : 6, strokeColor: color, strokeOpacity: isSelected ? 1.0 : 0.95, strokeStyle: "solid", endArrow: true });
      innerPl.setMap(mapInstance.current);
      polylinesRef.current[key] = { outer: outerPl, inner: innerPl };
    });

    if (hasPolyline) {
      mapInstance.current.setBounds(bounds);
    } else if (startPos && endPos) {
      bounds.extend(new kakao.maps.LatLng(startPos.lat, startPos.lng));
      bounds.extend(new kakao.maps.LatLng(endPos.lat, endPos.lng));
      mapInstance.current.setBounds(bounds);
    }
  }, [routes, mode, selectedRoute]);

  const handleReset = () => {
    if (startMarkerRef.current) startMarkerRef.current.setMap(null);
    if (endOverlayRef.current)  endOverlayRef.current.setMap(null);
    startMarkerRef.current = null;
    endOverlayRef.current  = null;
    pickStepRef.current    = 0;
    Object.values(polylinesRef.current).forEach(({ outer, inner }) => { outer?.setMap(null); inner?.setMap(null); });
    polylinesRef.current = {};
    setStartPos(null); setEndPos(null); setRoutes(null);
    setRecommendation(""); setContextTags([]); setWarnings([]); setWeather(null);
    setPhase("idle"); setSelectedRoute(null);
  };

  const handleSelectRoute = (key) => {
    setSelectedRoute(key);
    Object.entries(polylinesRef.current).forEach(([k, { outer, inner }]) => {
      const sel = k === key;
      outer?.setOptions({ strokeWeight: sel ? 16 : 10, strokeOpacity: sel ? 1.0 : 0.4 });
      inner?.setOptions({ strokeWeight: sel ? 10 : 6,  strokeOpacity: sel ? 1.0 : 0.3 });
    });
  };

  const openKakaoNavi = () => {
    if (!startPos || !endPos) return;
    const url = `https://map.kakao.com/link/from/출발지,${startPos.lat},${startPos.lng}/to/목적지,${endPos.lat},${endPos.lng}`;
    window.open(url, "_blank");
  };

  const originLabel = startPos ? `${startPos.lat.toFixed(4)}, ${startPos.lng.toFixed(4)}` : "출발지 선택";
  const destLabel   = endPos   ? `${endPos.lat.toFixed(4)}, ${endPos.lng.toFixed(4)}`   : "도착지 선택";

  // context 경로는 자동차 모드 불가 → walk로 폴백
  const contextMode = mode === "car" ? "walk" : mode;

  // ── 패널 콘텐츠 ─────────────────────────────────────────────────────────
  const panel = (
    <>
      <PanelHead
        title="경로 비교"
        sub={
          phase === "loading" ? <><Dots /></>
          : phase === "error"   ? "연결 실패"
          : phase === "empty"   ? "결과 없음"
          : phase === "ready"   ? "2개 추천"
          : "출발·도착 선택"
        }
        action={
          (startPos || endPos) && (
            <button onClick={handleReset}
              className="border border-line bg-card rounded-[10px] px-3 py-1.5 text-[13px] font-semibold text-muted whitespace-nowrap">
              새 탐색
            </button>
          )
        }
      />

      <div className="flex-1 overflow-y-auto p-4 md:p-5">
        <ODPill origin={originLabel} dest={destLabel} />

        {phase === "loading" && (
          <>
            <SkeletonRouteCard />
            <div className="h-3" />
            <SkeletonRouteCard />
          </>
        )}

        {phase === "error" && (
          <StateView Icon={IconWifiOff} tone="error"
            title="경로를 불러오지 못했어요"
            desc="서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요."
            primary="다시 시도" onPrimary={handleReset} />
        )}

        {phase === "empty" && (
          <StateView Icon={IconMapOff} tone="warn"
            title="추천할 경로가 없어요"
            desc="출발지와 도착지가 너무 가깝거나 길 데이터가 없어요."
            primary="출발·도착 다시 선택" onPrimary={handleReset} />
        )}

        {phase === "idle" && (
          <p className="text-center text-muted text-sm mt-6">
            지도에서 출발지와 도착지를 선택하면<br />경로를 추천해 드립니다.
          </p>
        )}

        {phase === "ready" && (
          <>
            <RecommendationBanner weather={weather} recommendation={recommendation} contextTags={contextTags} />

            {warnings.length > 0 && (
              <div className="flex flex-col gap-1.5 mb-4">
                {warnings.map((w, i) => (
                  <div key={i} className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-xs text-amber-800">{w}</div>
                ))}
              </div>
            )}

            <SegMode mode={mode} setMode={setMode} />

            <div className="flex flex-col gap-3 pb-6">
              <RouteCard
                tone="weather" Icon={IconSun} title="날씨 최적 경로" recommended
                description={routes?.context?.description || "날씨 맞춤 · 쾌적한 경로"}
                eta={calcTravelTime(routes?.context, contextMode)}
                distance={formatDistance(routes?.context, contextMode)}
                selected={selectedRoute === "context"}
                onSelect={() => handleSelectRoute("context")}
                onNavigate={openKakaoNavi}
              />
              <RouteCard
                tone="primary" Icon={IconClock} title="최단 경로"
                description={routes?.normal?.description || "시간 최단 · 카카오 내비"}
                eta={calcTravelTime(routes?.normal, mode)}
                distance={formatDistance(routes?.normal, mode)}
                selected={selectedRoute === "normal"}
                onSelect={() => handleSelectRoute("normal")}
                onNavigate={openKakaoNavi}
              />
              {/* 커스텀 경로: 비어있으면 /draw로 유도 */}
              <div
                className="flex items-center gap-3 p-4 bg-card rounded-card border border-line shadow-sm cursor-pointer hover:shadow-md transition-shadow"
                onClick={() => navigate("/draw")}
              >
                <div className="w-10 h-10 rounded-xl bg-chip grid place-items-center shrink-0">
                  <IconStar size={20} className="text-faint" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-bold text-ink">커스텀 경로</div>
                  <div className="text-[12.5px] text-faint">직접 경로를 그려보세요 →</div>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );

  // ── 지도 오버레이 ────────────────────────────────────────────────────────
  const map = (
    <>
      {phase === "loading" && (
        <div className="absolute top-5 left-1/2 -translate-x-1/2">
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-card shadow-md border border-line text-[13px] font-bold text-ink whitespace-nowrap">
            <Spinner size={16} /> 경로 계산 중 <Dots />
          </div>
        </div>
      )}
      {phase === "idle" && !startPos && (
        <div className="absolute top-5 left-1/2 -translate-x-1/2 pointer-events-none">
          <div className="px-4 py-2 rounded-full bg-card shadow-md border border-line text-[13px] font-semibold text-ink whitespace-nowrap">
            📍 출발지를 지도에서 클릭하세요
          </div>
        </div>
      )}
      {phase === "idle" && startPos && !endPos && (
        <div className="absolute top-5 left-1/2 -translate-x-1/2 pointer-events-none">
          <div className="px-4 py-2 rounded-full bg-card shadow-md border border-line text-[13px] font-semibold text-ink whitespace-nowrap">
            🏁 도착지를 지도에서 클릭하세요
          </div>
        </div>
      )}
      {phase === "ready" && (
        <div className="absolute right-5 top-5">
          <Legend />
        </div>
      )}
    </>
  );

  const sheetHeader = (
    <div className="flex items-center gap-2">
      <h1 className="m-0 text-[20px] font-extrabold text-ink tracking-[-0.02em]">경로 비교</h1>
      <span className="text-[13px] text-faint">
        {phase === "loading" ? "계산 중" : phase === "ready" ? "2개 추천" : ""}
      </span>
    </div>
  );

  return <MapScreenScaffold mapRef={mapRef} panel={panel} map={map} sheetHeader={sheetHeader} />;
}
