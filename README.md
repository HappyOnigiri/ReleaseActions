# ReleaseActions

リリースフロー用の共通 Composite Action 群。`HappyOnigiri/ShareSettings` の `actions/` から移設したもの。

3 つの Action は `release/vX.Y.Z` というブランチ名の規約を共有しているため、同一リポジトリで管理する。

| Action | 役割 | 呼び出し契機 |
| --- | --- | --- |
| `actions/prepare-release` | バージョンを決めてリリースブランチと PR を作成 | `workflow_dispatch`（手動） |
| `actions/publish-release` | タグと GitHub Release を作成し、リリースブランチを削除 | リリース PR のマージ |
| `actions/release-reminder` | 前回リリース以降のマージ済み PR 数を PR にコメント | main 宛の PR |

`actions/_lib/common.sh` は Action ではなく、3 つの Action が共有するシェル関数（エラー報告・入力検証・バージョン読み取り）。各ステップから `${{ github.action_path }}/../_lib/common.sh` として読み込む。

> このリポジトリは public である必要がある。ユーザーアカウント配下の private リポジトリの Action は、他リポジトリから `uses:` で参照できないため。

## リリースの流れ

1. 呼び出し側リポジトリで Release ワークフローを手動実行する（バージョンまたは bump 種別を指定）
2. `prepare-release` が `release/vX.Y.Z` ブランチと、changelog を本文に持つ `release: vX.Y.Z` PR を作成する
3. PR の CI が通ったらマージする
4. `publish-release` がタグ `vX.Y.Z` と GitHub Release を作成し、リリースブランチを削除する

## エラーの扱い

失敗したときに実行ログを追わなくても原因が分かるよう、3 つの Action は共通ヘルパー `actions/_lib/common.sh` の `fail` でエラーを報告する。出力は次の 2 か所。

- `::error::` アノテーション: 何が起きたかの要約 1 行（Actions の実行画面の上部に出る）
- `$GITHUB_STEP_SUMMARY`: 要約に加えて、現在の状態（取得値・パス・API の応答）と対処方法

外部コマンド（`actions/checkout`、`npm`、`gh`、`git push`）が呼び出し元では意味の取れないエラーを出すケースは、可能な限り手前で検証して自前のメッセージに置き換えてある。たとえば未登録のシークレットは空文字に展開されるため、そのまま渡すと `actions/checkout` が `Input required and not supplied: token` で落ちるが、この Action は入力検証の段階で「シークレットが未設定である」ことを指摘する。

## actions/prepare-release

バージョンの指定方法は 2 通りで、**同時に指定するとエラー**になる。どちらも指定しない場合もエラー。

- `version`: `1.3.0` のように semver を直接指定する
- `bump`: `major` / `minor` / `patch` / `auto` のいずれかを指定し、現在のバージョンから算出する

### bump の起点

現在のバージョンは `version-bump-type` に対応する真実源から読む。`none` の場合のみ最新タグを起点にする。

| `version-bump-type` | 起点 |
| --- | --- |
| `npm` | `package.json` の `version` |
| `python` | `python-version-file` の `__version__` |
| `pyproject` | `pyproject-path` のトップレベル `version` |
| `xcodegen` | `xcodegen-version-file` の `MARKETING_VERSION` |
| `none` | 最新タグ（`vX.Y.Z`） |

### bump: auto の判定ルール

前回 GitHub Release の `publishedAt` 以降に main へマージされた PR の**タイトル**から判定する。changelog の分類（`release-changelog-builder-action` の `label_extractor`）と同じ規約に揃えてある。

- `feat:` の PR が 1 件でもあれば **minor**
- それ以外（`fix` / `perf` / `refactor` / `chore` / `ci` / `docs` / `build` / `style` / `test`）のみなら **patch**
- `feat!:` のような `!` 付き、または本文に `BREAKING CHANGE:` を含む PR を検出した場合は**エラーで停止**する。major は自動では上げず、`bump: major` か `version` の明示指定を求める
- 対象 PR が 0 件、またはどの prefix にも当たらない場合もエラーで停止する（暗黙の patch にはしない）
- 自身のリリース PR（`release: vX.Y.Z`）は判定対象から除外する

判定結果と対象 PR は `$GITHUB_STEP_SUMMARY` に出力される。

### 実行前に検出する問題

ブランチや PR を作る前に次を検証し、該当した場合はその時点で停止する。

| 検出する問題 | 判定内容 |
| --- | --- |
| トークン未設定 | `github-token` が空（シークレットの未登録・名前違い） |
| トークン無効・権限不足 | リポジトリの API 取得に失敗、または `permissions.push` が `false` |
| 入力の矛盾 | `version` と `bump` の同時指定、両方未指定、semver 以外の `version`、未知の `bump` / `version-bump-type` |
| bump の起点が無い | 真実源のファイルが無い・バージョンが semver でない・`none` で既存タグが無い |
| auto 判定不能 | GitHub Release が無い、対象 PR が 0 件、prefix が付いていない、breaking change を検出 |
| バージョン重複 | タグ `vX.Y.Z` が存在、GitHub Release `vX.Y.Z` が存在、真実源の現在バージョンと同値 |
| バージョン逆行 | 指定バージョンが真実源の現在バージョンより小さい |
| bump 対象の欠落 | `package.json` / バージョンファイル / `pyproject.toml` / `project.yml` が無い、書き換え対象の記述が無い |
| コミット対象が空 | バージョン更新後に差分が無い |
| push / PR 作成の失敗 | ブランチ保護、権限不足、同名バージョンの PR が既に merged / closed |

同じバージョンを 2 度リリースしようとした場合は、`npm version` の `Version not changed` ではなく「リリースバージョン X は package.json の現在のバージョンと同じです」と、対処つきで報告する。

なお、ブランチ `release/vX.Y.Z` が既にある場合はエラーにせず、main の内容で作り直して既存の PR を更新する（`::notice::` を出す）。

### 入力

| 名前 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `version` | - | `""` | semver バージョン（例 `1.3.0`）。`bump` と同時指定不可 |
| `bump` | - | `none` | `none` / `auto` / `major` / `minor` / `patch` |
| `version-bump-type` | - | `none` | `npm` / `python` / `pyproject` / `xcodegen` / `none` |
| `python-version-file` | - | `src/__version__.py` | `version-bump-type=python` のときのバージョンファイル |
| `pyproject-path` | - | `pyproject.toml` | `version-bump-type=pyproject` のときの pyproject.toml |
| `xcodegen-version-file` | - | `project.yml` | `version-bump-type=xcodegen` のときの project.yml |
| `post-bump-command` | - | `""` | bump 後・commit 前に実行するコマンド（pyproject 向け） |
| `extra-paths-to-commit` | - | `""` | bump コミットに追加で含めるパス（pyproject 向け） |
| `github-token` | ✓ | - | `contents:write` + `pull-requests:write` 権限の PAT |

### 出力

| 名前 | 説明 |
| --- | --- |
| `version` | 実際にリリースするバージョン（bump 指定時は算出結果） |

### 使用例

```yaml
name: Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: "Release version (e.g. 1.3.0)。bump を使う場合は空にする"
        required: false
        type: string
      bump:
        description: "バージョンの上げ方"
        required: false
        default: none
        type: choice
        options: [none, auto, major, minor, patch]

permissions:
  contents: write
  pull-requests: write

jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - uses: HappyOnigiri/ReleaseActions/actions/prepare-release@main
        with:
          version: ${{ inputs.version }}
          bump: ${{ inputs.bump }}
          version-bump-type: npm
          github-token: ${{ secrets.GH_TOKEN }}
```

## actions/publish-release

マージされた `release/vX.Y.Z` ブランチからバージョンを取り出し、タグと GitHub Release を作成してブランチを削除する。

| 名前 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `branch` | ✓ | - | マージされたリリースブランチ名（例 `release/v1.3.0`） |
| `pr-body` | ✓ | - | PR 本文（リリースノートとして使う） |
| `update-major-tag` | - | `false` | `v1` のようなメジャータグを更新するか |
| `github-token` | ✓ | - | `contents:write` 権限の PAT |

トークン未設定・無効、`branch` が `release/vX.Y.Z` 形式でない、`update-major-tag` が `true` / `false` 以外、タグ push・Release 作成・ブランチ削除の失敗を、それぞれ対処つきで報告する。タグや Release が既にある場合は再実行とみなして続行する。ブランチ削除だけが失敗した場合は、タグと Release の作成が完了していることをメッセージに含める。

## actions/release-reminder

main 宛の PR に、前回リリース以降のマージ済み PR 数をコメントする。`synchronize` 時は既存コメントを更新する。

head ブランチが `release/v*` の PR（`prepare-release` が作るリリース PR 自身）は対象外で、何もせず終了する。

| 名前 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `threshold` | - | `10` | この件数以上で「リリースを検討してください」を付ける |
| `github-token` | ✓ | - | `pull-requests:write` 権限のトークン |

トークン未設定、`pull_request` 以外のイベントで呼ばれて PR 番号が取れない、`threshold` が数値でない、PR 一覧の取得やコメントの失敗を、それぞれ対処つきで報告する。前回リリースがまだ無い場合はエラーにせず何もせず終了する。
