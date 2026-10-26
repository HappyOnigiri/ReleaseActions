#!/usr/bin/env bash
set -euo pipefail
# shellcheck source=actions/_lib/common.sh
source "${ACTION_PATH}/../_lib/common.sh"

notes=$(mktemp "${RUNNER_TEMP}/release-notes.XXXXXX")
trap 'rm -f "$notes"' EXIT
printf '%s\n' "$BODY" > "$notes"

if [ -n "${ASSET_DIR:-}" ]; then
  if draft=$(gh release view "$TAG" --json isDraft --jq .isDraft 2>/dev/null); then
    case "$draft" in
      false)
        echo "::notice::Release ${TAG} は公開済みのため、本文・添付ファイル・latest を変更しません"
        exit 0
        ;;
      true) ;;
      *) fail "Release ${TAG} の draft 状態を確認できません" \
           "取得値: ${draft}" "対処: GitHub API の状態を確認して再実行してください" ;;
    esac
  elif ! output=$(gh release create "$TAG" --title "$TAG" --notes-file "$notes" --verify-tag --draft 2>&1); then
    fail "draft Release ${TAG} を作成できませんでした" \
      "$output" "対処: トークンの権限と GitHub API の状態を確認して再実行してください"
  fi
fi

# PR 作成後に main に入った変更と、前回のマージ後・公開前の変更も拾う。
# 公開済み Release の内容は成果物付き再実行では従来どおり保持する。
if [ -n "${GITHUB_REPOSITORY:-}" ]; then
  previous=$(gh release list --exclude-drafts --exclude-pre-releases --limit 100 \
    --json tagName --jq "[.[] | select(.tagName != \"${TAG}\")][0].tagName // \"\"")
  if ! changes=$(RELEASE_NOTES=true python3 "${ACTION_PATH}/../_lib/release-prs.py" "$previous" HEAD); then
    fail "リリース対象 PR の取得に失敗しました" \
      "対処: GitHub API とタグを確認し、公開処理を再実行してください"
  fi
  if [ -n "$changes" ]; then
    printf '\n## Changes included in this release\n\n%s\n' "$changes" >> "$notes"
  fi
fi

# 添付を指定しない既存の呼び出しは、公開済み Release の本文更新も含めて維持する。
if [ -z "${ASSET_DIR:-}" ]; then
  if gh release view "$TAG" >/dev/null 2>&1; then
    if ! output=$(gh release edit "$TAG" --notes-file "$notes" 2>&1); then
      fail "既存の GitHub Release ${TAG} を更新できませんでした" \
        "$output" "対処: github-token の contents:write 権限を確認してください"
    fi
  elif ! output=$(gh release create "$TAG" --title "$TAG" --notes-file "$notes" 2>&1); then
    fail "GitHub Release ${TAG} を作成できませんでした" \
      "$output" "対処: github-token の contents:write 権限と、タグが push されているかを確認してください"
  fi
  exit 0
fi

# 再実行時の部分アップロードは draft 内で置換し、全件成功したときだけ公開する。
shopt -s nullglob
files=("${ASSET_DIR}/"*)
if [ "${#files[@]}" -eq 0 ]; then
  fail "退避した添付ファイルが見つかりません" \
    "対処: 成果物のビルドからワークフローを再実行してください。Release は draft のままです"
fi
if ! output=$(gh release upload "$TAG" "${files[@]}" --clobber 2>&1); then
  fail "Release ${TAG} の添付に失敗しました" \
    "$output" "対処: ワークフローを再実行してください。Release は draft のままで、latest は変更していません"
fi
if ! output=$(gh release edit "$TAG" --notes-file "$notes" --draft=false 2>&1); then
  fail "Release ${TAG} の公開を完了できませんでした" \
    "$output" "対処: GitHub 上の公開状態を確認して再実行してください。公開済みなら添付は変更しません"
fi
