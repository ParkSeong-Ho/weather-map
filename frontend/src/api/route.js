import { api } from "./client";

/**
 * 추천 경로 데이터를 백엔드에서 가져옵니다.
 * 백엔드: GET /api/route/recommend
 *
 * @param {number} startLat - 출발지 위도
 * @param {number} startLng - 출발지 경도
 * @param {number} endLat - 도착지 위도
 * @param {number} endLng - 도착지 경도
 *
 * @returns {Promise<{
 *   routes: object,
 *   context_tags: string[],
 *   recommendation: string
 * }>}
 */

/**
 * 클릭한 점들을 보행자 도로에 스냅한 폴리라인을 가져옵니다.
 * 백엔드: POST /api/route/snap
 * 순환 코스(시작점 ≈ 도착점)도 경유지를 따라 실제 길이 반환됩니다.
 *
 * @param {{lat: number, lng: number}[]} points - 클릭 순서대로의 좌표 목록 (2개 이상)
 * @returns {Promise<{polyline: {lat: number, lng: number}[], distance: number}>}
 */
export const fetchSnappedPath = async (points) => {
  const { data } = await api.post("/api/route/snap", { points });
  return data;
};

export const fetchRouteRecommend = async (
  startLat,
  startLng,
  endLat,
  endLng
) => {
  const { data } = await api.get("/api/route/recommend", {
    params: {
      start_lat: startLat,
      start_lng: startLng,
      end_lat: endLat,
      end_lng: endLng,
    },
  });

  return data;
};