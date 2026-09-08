#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=actions/_lib/common.sh
source "${ACTION_PATH}/../_lib/common.sh"

# checkout の clean から成果物を守るため、runner の一時領域へ先にコピーする。
directory=$(mktemp -d "${RUNNER_TEMP}/release-assets.XXXXXX")
count=0
while IFS= read -r path || [ -n "$path" ]; do
  [ -n "$path" ] || continue
  if [ ! -f "$path" ] || [ ! -r "$path" ]; then
    fail "添付ファイル '${path}' を読み込めません" \
      "対処: ビルド完了後に実在するファイルのパスを assets に改行区切りで渡してください（glob 不可）"
  fi
  name=$(basename "$path")
  if ! [[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
    fail "添付ファイル名 '${name}' は使用できません" \
      "対処: ファイル名は英数字で始め、英数字・ピリオド・ハイフン・アンダースコアだけを使ってください"
  fi
  if [ -e "${directory}/${name}" ]; then
    fail "添付ファイル名 '${name}' が重複しています" \
      "対処: Release 上で一意になるようファイル名を変更してください"
  fi
  cp -- "$path" "${directory}/${name}"
  count=$((count + 1))
done <<< "$RELEASE_ASSETS"

if [ "$count" -eq 0 ]; then
  fail "assets に添付ファイルが指定されていません" \
    "対処: ファイルパスを指定するか、添付なしで公開する場合は assets 入力を省略してください"
fi
echo "directory=${directory}" >> "$GITHUB_OUTPUT"
