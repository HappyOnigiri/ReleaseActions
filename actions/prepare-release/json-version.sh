#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=actions/_lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/common.sh"

mode="$1"
file_list="$2"
next="${3:-}"
case "$mode" in
  read|bump) ;;
  *) fail "JSON バージョンの操作は read または bump で指定してください" ;;
esac

paths=()
current=""
# 全ファイルを検証してから更新する。不正な JSON やバージョン不一致で一部だけ更新しない。
while IFS= read -r path || [ -n "$path" ]; do
  path="${path%$'\r'}"
  [ -z "$path" ] && continue
  if [ ! -f "$path" ]; then
    fail "JSON バージョンファイル ${path} が見つかりません" \
      "対処: json-version-files をリポジトリルートからのパスで指定してください"
  fi
  if ! value=$(jq -ser '
    if length == 1 and (.[0] | type == "object") and (.[0].version | type == "string")
    then .[0].version else error("version must be a string in one JSON object") end
  ' -- "$path"); then
    fail "${path} のトップレベル version を読み取れません" \
      "対処: JSON オブジェクトに文字列の version を指定してください"
  fi
  if [[ "$value" == *$'\n'* ]] || ! is_semver "$value"; then
    fail "${path} の version '${value}' は semver ではありません" \
      "対処: version を X.Y.Z 形式にしてください"
  fi
  if [ -n "$current" ] && [ "$value" != "$current" ]; then
    fail "JSON ファイル間でバージョンが一致しません (${path}: ${value}, 起点: ${current})" \
      "対処: json-version-files の全ファイルを同じバージョンにそろえてください"
  fi
  current="$value"
  paths+=("$path")
done <<< "$file_list"

if [ "${#paths[@]}" -eq 0 ]; then
  fail "json-version-files が空です" \
    "対処: 対象 JSON ファイルを改行区切りで指定してください"
fi

if [ "$mode" = "read" ]; then
  printf '%s\n' "$current"
  exit 0
fi
if [[ "$next" == *$'\n'* ]] || ! is_semver "$next" || ! version_gt "$next" "$current"; then
  fail "更新先の JSON バージョンは ${current} より大きい X.Y.Z 形式で指定してください"
fi

temporary=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/json-version.XXXXXX")
trap 'rm -f "$temporary"' EXIT
for path in "${paths[@]}"; do
  jq --arg version "$next" '.version = $version' -- "$path" > "$temporary"
  cat "$temporary" > "$path"
  git add -- "$path"
done
