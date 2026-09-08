#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=actions/_lib/common.sh
source "${ACTION_PATH}/../_lib/common.sh"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1; then
  # 成果物付きの再実行では、別コミットのバイナリを同じタグへ混ぜない。
  if [ -n "${ASSET_DIR:-}" ] && [ "$(git rev-parse "refs/tags/${TAG}^{commit}")" != "$(git rev-parse HEAD)" ]; then
    fail "タグ ${TAG} とリリース対象コミットが一致しません" \
      "tag: $(git rev-parse "refs/tags/${TAG}^{commit}"), target: $(git rev-parse HEAD)" \
      "対処: タグと同じコミットから成果物をビルドし、target-commit を揃えて再実行してください"
  fi
  echo "::notice::タグ ${TAG} は既に存在するため作成をスキップします"
  exit 0
fi

git tag "$TAG"
if ! PUSH_OUT=$(git push origin "$TAG" 2>&1); then
  fail "タグ ${TAG} を push できませんでした" \
    "git の出力: $(echo "$PUSH_OUT" | grep -v '^remote: *$' | tail -3 | tr '\n' ' ')" \
    "対処: github-token の contents:write 権限と、タグ保護ルールを確認してください"
fi
