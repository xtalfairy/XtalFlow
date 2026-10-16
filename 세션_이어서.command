#!/bin/bash
# XtalFlow 작업 세션을 이어서 엽니다 (더블클릭용)
# 만든 날: 2026-09-23

PROJ="/Users/jjh/Dropbox/12_codebase/XtalFlow"
SESSION="0df41649-227a-493e-a8e8-19072addd610"
CLAUDE="/Users/jjh/.local/bin/claude"

# 이 세션은 상위 폴더에서 시작해 기록이 그쪽에 남아 있어요.
HERE="$HOME/.claude/projects/-Users-jjh-Dropbox-12-codebase-XtalFlow/$SESSION.jsonl"
THERE="$HOME/.claude/projects/-Users-jjh-Dropbox-12-codebase/$SESSION.jsonl"

cd "$PROJ" || { echo "작업 폴더를 찾을 수 없어요: $PROJ"; read -n1 -p "엔터를 누르면 닫혀요"; exit 1; }

echo "=============================================="
echo " XtalFlow 작업 세션"
echo " 폴더 : $PROJ"
echo " 세션 : $SESSION"
echo "=============================================="
echo

# 같은 세션이 이미 떠 있는지 확인
RUNNING=$(pgrep -fl "claude" | grep -v "$$" | grep -c "$SESSION")
if [ "$RUNNING" -gt 0 ]; then
  echo "⚠️  이 세션이 이미 다른 창에서 실행 중인 것 같아요."
  echo "   같은 세션을 두 곳에서 열면 대화 기록이 섞일 수 있어요."
  echo
  echo "   [1] 그래도 연다   [2] 새 세션으로 시작   [그 외] 취소"
  read -n1 -p "   선택: " ANS; echo
  case "$ANS" in
    1) ;;
    2) exec "$CLAUDE" ;;
    *) echo "   취소했어요."; sleep 1; exit 0 ;;
  esac
fi

# 대화 기록을 이 폴더 기준으로 한 번만 옮겨 둡니다(원본은 그대로 남겨요).
if [ ! -f "$HERE" ] && [ -f "$THERE" ]; then
  echo "이전 대화 기록을 이 폴더 기준으로 가져올게요 (한 번만, 약 $(du -h "$THERE" | cut -f1))..."
  mkdir -p "$(dirname "$HERE")" || exit 1
  cp "$THERE" "$HERE.part" && mv "$HERE.part" "$HERE" || {
    rm -f "$HERE.part"
    echo "가져오지 못했어요. 상위 폴더에서 이어서 열게요."
    cd /Users/jjh/Dropbox/12_codebase || exit 1
    exec "$CLAUDE" --resume "$SESSION"
  }
  echo "가져왔어요."
  echo
fi

if [ ! -f "$HERE" ]; then
  echo "이전 세션 기록을 찾을 수 없어요. 새 세션으로 열게요."
  exec "$CLAUDE"
fi

exec "$CLAUDE" --resume "$SESSION"
