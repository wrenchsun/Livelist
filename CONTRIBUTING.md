# Contributing to Livelist

## ブランチ運用

`main` ブランチへの直接 push は禁止です。必ずブランチを作成して Pull Request を送ってください。

### ブランチ命名規則

| prefix | 用途 | 例 |
|---|---|---|
| `feature/` | 新機能追加 | `feature/add-niconico-support` |
| `fix/` | バグ修正 | `fix/quota-not-counted-on-error` |
| `hotfix/` | リリース済みへの緊急修正 | `hotfix/crash-on-startup` |
| `docs/` | ドキュメントのみの変更 | `docs/update-api-reference` |
| `refactor/` | リファクタリング | `refactor/stream-classifier` |
| `test/` | テストの追加・修正 | `test/add-twitch-url-cases` |

```bash
git switch -c feature/your-feature-name
```

## コミットメッセージ

[Conventional Commits](https://www.conventionalcommits.org/) 形式を使用します。

```
<type>: <概要（命令形・50文字以内）>
```

| type | 用途 |
|---|---|
| `feat` | 新機能 |
| `fix` | バグ修正 |
| `docs` | ドキュメント |
| `style` | フォーマット（機能に影響なし） |
| `refactor` | リファクタリング |
| `test` | テスト |
| `chore` | ビルド・設定・依存関係 |

**例:**
```
feat: タグ AND/OR 切り替えを追加
fix: API クォータが失敗時にカウントされない問題を修正
docs: README にクォータ計算式を追記
test: TestIsMemberOnly にケースを追加
chore: build.bat に git 情報埋め込みを追加
```

## Pull Request

- `main` へのマージは PR 必須です
- PR タイトルもコミットメッセージと同じ形式にしてください
- PR テンプレートに従って内容を記入してください

## Issue

バグ報告・機能要望は Issue テンプレートを使用してください。

## リリース・タグ

リリースは `vMAJOR.MINOR.PATCH` 形式のタグで管理します（例: `v1.0.0`）。

| 変更の種類 | バージョン |
|---|---|
| 後方互換性のない変更 | MAJOR を上げる |
| 後方互換性のある新機能 | MINOR を上げる |
| バグ修正・細かい変更 | PATCH を上げる |

## 開発環境

```bash
# Python版で起動（サーバー再起動不要でUI変更が即反映）
python3.12 source/server.py

# テスト実行
python3.12 -m unittest discover -s tests -v

# exe ビルド
cd source
build.bat
```
