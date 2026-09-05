# ReleaseActions

リリースフロー用の共通 Composite Action 群。`HappyOnigiri/ShareSettings` の `actions/` から移設したもの。

3 つの Action は `release/vX.Y.Z` というブランチ名の規約を共有しているため、同一リポジトリで管理する。

| Action | 役割 | 呼び出し契機 |
| --- | --- | --- |
| `actions/prepare-release` | バージョンを決めてリリースブランチと PR を作成 | `workflow_dispatch`（手動） |
| `actions/publish-release` | タグと GitHub Release を作成し、リリースブランチを削除 | リリース PR のマージ |
| `actions/release-reminder` | 前回リリース以降のマージ済み PR 数を PR にコメント | main 宛の PR |

> このリポジトリは public である必要がある。ユーザーアカウント配下の private リポジトリの Action は、他リポジトリから `uses:` で参照できないため。

## リリースの流れ

1. 呼び出し側リポジトリで Release ワークフローを手動実行する（バージョンまたは bump 種別を指定）
2. `prepare-release` が `release/vX.Y.Z` ブランチと、changelog を本文に持つ `release: vX.Y.Z` PR を作成する
3. PR の CI が通ったらマージする
4. `publish-release` がタグ `vX.Y.Z` と GitHub Release を作成し、リリースブランチを削除する

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

## actions/release-reminder

main 宛の PR に、前回リリース以降のマージ済み PR 数をコメントする。`synchronize` 時は既存コメントを更新する。

head ブランチが `release/v*` の PR（`prepare-release` が作るリリース PR 自身）は対象外で、何もせず終了する。

| 名前 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `threshold` | - | `10` | この件数以上で「リリースを検討してください」を付ける |
| `github-token` | ✓ | - | `pull-requests:write` 権限のトークン |
