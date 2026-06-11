#!/usr/bin/env bash
# 시연용 날씨 강제 전환 (영상 촬영용 — 화면에 안 보임)
# 사용법: ./demo.sh [off|uv_high|uv_extreme|rain|snow|night]
# 인자 없이 실행하면 현재 상태 표시
API=${API:-http://localhost:8000}

if [ -z "$1" ]; then
  curl -s "$API/api/route/demo"; echo
else
  curl -s -X POST "$API/api/route/demo/$1"; echo
fi
