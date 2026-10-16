#!/bin/bash
# XtalFlow을 연습용 설정으로 엽니다 (더블클릭용)
# 만든 날: 2026-10-16
#
# 연습용이라 worksheet가 실제 장비로 가지 않고 아래 DATA 폴더에 저장돼요.
# 실제 장비로 보내려면 README의 "Viewer 실행" 절을 보세요.

PROJ="/Users/jjh/Dropbox/12_codebase/XtalFlow"
DATA="$HOME/Documents/XtalFlow-데모"
VENV="$PROJ/.venv_air"

cd "$PROJ" || { echo "작업 폴더를 찾을 수 없어요: $PROJ"; read -n1 -p "엔터를 누르면 닫혀요"; exit 1; }

if [ ! -f "$VENV/bin/activate" ]; then
  echo "가상환경을 찾을 수 없어요: $VENV"
  echo "저장소 폴더에서 아래를 먼저 실행해 주세요."
  echo "  python3 -m venv .venv_air"
  echo "  source .venv_air/bin/activate"
  echo "  python -m pip install -r requirements.txt"
  echo "  python -m pip install -e ."
  read -n1 -p "엔터를 누르면 닫혀요"; exit 1
fi

source "$VENV/bin/activate"
mkdir -p "$DATA/staging" "$DATA/echo650" "$DATA/shifter1" "$DATA/shifter2" || exit 1

echo "=============================================="
echo " XtalFlow (연습용)"
echo " 프로그램 : $PROJ"
echo " 데이터   : $DATA"
echo "=============================================="
echo "창을 닫으면 프로그램도 함께 종료돼요."
echo

exec xtalflow-viewer \
  --review-db "$DATA/reviews.sqlite3" \
  --library-dir chems \
  --worksheet-dir "$DATA/staging" \
  --echo-dir "$DATA/echo650" \
  --shifter1-dir "$DATA/shifter1" \
  --shifter2-dir "$DATA/shifter2" \
  --allow-local-instrument-dirs
