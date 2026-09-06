#!/usr/bin/env bash
# 各 Action のステップから source して使う共通ヘルパー。
#
# 目的は、失敗したときにログを追わなくても Actions の画面上で
# 「何が起きたか / 今どうなっているか / どう直すか」が読めるようにすること。

# fail <要約> [詳細行...]
#
# 要約を ::error:: アノテーションとして出し、要約と詳細を Job Summary にも残して終了する。
# 詳細行には現在の状態（取得値・パスなど）と、次に取るべき操作を書く。
fail() {
  local summary="$1"
  shift

  echo "::error::${summary}"

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### ❌ ${summary}"
      if [ "$#" -gt 0 ]; then
        echo ""
        local line
        for line in "$@"; do
          echo "- ${line}"
        done
      fi
      echo ""
    } >>"$GITHUB_STEP_SUMMARY"
  fi

  exit 1
}

# require_token <入力値> <入力名> <必要な権限の説明>
#
# 未設定のシークレットは空文字に展開されるため、そのまま渡すと
# actions/checkout や gh が「Input required and not supplied: token」のような
# 呼び出し元では意味が分からないエラーで落ちる。手前で弾く。
require_token() {
  local value="$1" name="$2" scopes="$3"

  if [ -z "$value" ]; then
    fail "${name} が空です。トークンのシークレットが設定されていません" \
      "呼び出し側ワークフローが渡している \`secrets.*\` が未登録か、名前が違う可能性があります" \
      "未定義のシークレットは空文字に展開されるため、この Action からは未設定と区別できません" \
      "対処: ${scopes} を持つトークンをリポジトリの Settings → Secrets and variables → Actions に登録し、その名前を \`${name}\` に渡してください"
  fi
}

# require_commands <コマンド名...>
require_commands() {
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      fail "コマンド \`${cmd}\` が見つかりません" \
        "この Action は runner に \`${cmd}\` があることを前提にしています" \
        "対処: \`${cmd}\` を用意するセットアップステップを、この Action の前に追加してください"
    fi
  done
}

# is_semver <値>
is_semver() {
  echo "$1" | grep -qE '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
}

# version_gt <a> <b> : a > b なら 0
version_gt() {
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}

# version_source_path <version-bump-type> <python-file> <pyproject> <xcodegen>
#
# 現在バージョンの真実源を人間が読める形で返す。エラーメッセージで
# 「どのファイルを直せばよいか」を示すために使う。
version_source_path() {
  case "$1" in
    npm) echo "package.json" ;;
    python) echo "$2" ;;
    pyproject) echo "$3" ;;
    xcodegen) echo "$4" ;;
    none) echo "最新タグ" ;;
    *) echo "(unknown)" ;;
  esac
}

# read_current_version <version-bump-type> <python-file> <pyproject> <xcodegen>
#
# 真実源から現在のバージョンを読む。読めない場合は空文字を返し、
# エラーにするかどうかは呼び出し側が判断する（version 明示指定時は不要なため）。
read_current_version() {
  local type="$1" py_file="$2" pyproject="$3" xcodegen="$4" current=""

  case "$type" in
    npm)
      [ -f package.json ] && current=$(jq -r '.version // ""' package.json)
      ;;
    python)
      [ -f "$py_file" ] && current=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$py_file" | head -1)
      ;;
    pyproject)
      [ -f "$pyproject" ] && current=$(sed -n 's/^version = "\(.*\)"/\1/p' "$pyproject" | head -1)
      ;;
    xcodegen)
      [ -f "$xcodegen" ] && current=$(sed -n 's/.*MARKETING_VERSION: "\(.*\)"/\1/p' "$xcodegen" | head -1)
      ;;
    none)
      current=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
      current="${current#v}"
      ;;
  esac

  echo "$current"
}
