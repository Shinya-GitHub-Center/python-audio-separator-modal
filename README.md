# python-audio-separator-modal

## このリポジトリの目的
- 大好きなバンド音源を分離して、主にドラムとベースとギターとボーカル部分を個人使用目的のみでローカルPCで再生させ気持ちよくなるプロジェクトです。
- 最初ローカルマシンで生成したところ、CPUファンが狂ったように回りだしたので、せっかくなので[modal](https://modal.com/)というコストパフォーマンスの高いクラウドサービスを利用し、リモート提供GPU上で実行するようにした。
- 著作権のあるファイルを扱うため、リモート環境には保存されないようにした（モデルのみリモート永続ディスクに保存）
- とはいっても、実際はZedエディタ内蔵のAIエージェントにアイディアのみ渡して、ほぼ自動で作成したもらった（一発で上手くいったのでAIの進化にビビりまくった笑）

## 事前準備
```
$ uv sync
$ source .venv/bin/activate
```

および、modalサーバー上にこのプロジェクト専用のEnvironment作成を推奨。

## 特記事項
- `Separator`モジュール等リモート環境でのみ参照されるものは、ローカルでは参照できないためエラーになるので、これを回避するために該当箇所の文末に`# pyright: ignore[reportMissingImports]`というコメントを追加した。

## `separate.py`（音源分離アプリ）の設計方針

Modal GPU上で音源分離（ボーカル/楽器分離）を行う簡易アプリとして`separate.py`を用意した。以下は検討した選択肢とその判断根拠。

### 1. 使用ライブラリ：`python-audio-separator`

音源分離には [`nomadkaraoke/python-audio-separator`](https://github.com/nomadkaraoke/python-audio-separator) を採用した。

比較検討した候補：

| | `python-audio-separator`（採用） | `ZFTurbo/Music-Source-Separation-Training` |
|---|---|---|
| 概要 | UVR (Ultimate Vocal Remover) で使われる学習済みモデル群を統一APIでラップしたpipパッケージ | BS-Roformer / Mel-Band Roformer等の学習コード＋チェックポイント配布元（本家） |
| 導入 | `pip install audio-separator[gpu]` のみ | GitHubをclone + モデルごとにconfig(yaml)/checkpoint(ckpt)を個別に紐付ける必要あり |
| 品質 | 中身は同じコミュニティ（viperx, unwa, becruilyなど）製の最新チェックポイント（BS-Roformer / Mel-Band Roformerなど）を利用可能。実質同等の品質 | オリジナル。SDRベンチマークの最新値を追いやすい |
| Modalとの相性 | PyPI経由の`uv_pip_install`一発で入る。公式にModalへのデプロイ手順も同梱されている（`audio_separator/remote/`） | `git clone`+ ローカルpathインストールが必要（ACE-Stepと同系統の手間） |

Demucsより新しく現在も活発に開発されている(2026年時点で直近リリースあり、月次以上の頻度で更新)、かつ品質面でもTransformerベースのBS-Roformer/Mel-Band Roformer系モデル(SDR 12.9dB前後、Demucsの旧世代モデルより高精度)を扱える点が決め手。

~~既定モデル(`DEFAULT_MODEL`)には、楽器単位でのフル分解(vocals/drums/bass/guitar/piano/other の6-stem)ができる`htdemucs_6s.yaml`(Demucs v4)を採用した。~~ 既存モデルは4-stemバージョンにした。2-stem(vocals/instrumental)のみで良い場合や、より高いSDR(分離精度)を優先したい場合は、`model_bs_roformer_ep_317_sdr_12.9755.ckpt`(viperx版BS-Roformer)のように`--model-filename`引数で他モデルに切り替え可能。利用可能なモデル一覧は`audio-separator --list_models`で確認できる。

### 2. 元ファイルのアップロード方法：`local_entrypoint`でbytesを直接渡す方式

検討した2案：

- **案A：永続Volumeに`modal volume put`で手動アップロード** → 大量ファイルの使い回しには向くが、コマンドが2段階になり簡易アプリの趣旨に反する。入力ファイル専用の永続Volumeの管理・クリーンアップという運用コストも増える。
- **案B（採用）：`local_entrypoint`でローカルファイルを読み込み、bytesとして`.remote()`に直接渡す** → `modal run separate.py --input-path ./song.mp3` の1コマンドで完結する。`main.py`が生成結果のbytesを`.remote()`の戻り値として受け取りローカル保存しているのと対称的なロジックであり、実装の一貫性が高い。Modalは大きめの関数引数も自動的にblobストレージ経由でアップロードする仕組みを持つため、数MB〜数十MB程度のmp3/wavであれば問題なく渡せる（[Modal公式ドキュメント: Passing local data](https://modal.com/docs/guide/local-data)）。

音源分離は「手元のファイルを渡して結果を受け取るだけ」のテスト用途であり、入力ファイルをModal側に残しておく必然性がないため、案Bを採用した。

なお、**モデルの重み（チェックポイント）のキャッシュ用途としてはModal Volumeを引き続き使用している**。これは`main.py`の`model_cache`（ACE-Stepの重み保存用）と同じ役割で、ユーザーの入力データではなくアプリ側の資産を永続化する話であり、上記のアップロード方式の議論とは別軸の判断。

### 3. 分離結果（ステム）の保存先：プロジェクト内ローカルディレクトリ

GPU側の`AudioSeparator.run()`はステムごとのbytesを辞書（例：`{"vocals.wav": b"...", "instrumental.wav": b"..."}`）として返し、`local_entrypoint`側でプロジェクト内の`separated/<元ファイル名>/`ディレクトリに書き出す。永続Volumeへの保存は行わない（テストのたびにローカルで結果を確認できれば十分なため）。

### 4. Web UI（Gradio）は用意しない

音楽生成はプロンプト/歌詞の試行錯誤や他者へのデモ共有に価値があるため常時起動のWeb UI（`modal deploy`）を用意したが、音源分離は「ファイルを渡して結果を受け取るだけ」の単純作業であり、CLI（`modal run`）で完結させる方がシンプルで、`max_containers=1`のsticky session管理のような複雑さも不要と判断した。将来的にドラッグ&ドロップでのアップロードが欲しくなった場合も、`gr.File()`等を後から追加するだけで拡張可能なため、現時点でのスコープ外とした。

### 5. GPUサイズ

拡散モデルであるACE-Step（`main.py`、L40S使用）と異なり、Roformer系の音源分離推論は軽量なため、`separate.py`ではより安価なT4を使用している（[audio-separator公式ドキュメント](https://github.com/nomadkaraoke/python-audio-separator)でもT4での運用コスト試算が例示されている）。

### まとめ

| 項目 | 採用した方式 |
|---|---|
| ライブラリ | `python-audio-separator`（PyPI、BS-Roformer / Mel-Band Roformer等をラップ） |
| GPU | T4 |
| モデル重みキャッシュ | Modal Volume（`main.py`の`model_cache`と同じ役割） |
| 入力ファイル | `local_entrypoint`でローカルファイルを読み込み、bytesとして`.remote()`に直接渡す |
| 出力ファイル | `.remote()`の戻り値（bytesの辞書）をプロジェクト内ローカルディレクトリ（`separated/`）に保存 |
| Web UI | なし（`modal run`のみで完結） |

### 実行方法

```shell
# 既定(4-stem: vocals/drums/bass/other)
modal run separate.py --input-path ./song.mp3
# または
make separate INPUT=./song.mp3

# モデルを明示指定(例: 2-stemで高品質なvocals/instrumental分離)
modal run separate.py --input-path ./song.mp3 --model-filename model_bs_roformer_ep_317_sdr_12.9755.ckpt
# 例：6-stem
modal run separate.py --input-path ./song.mp3 --model-filename htdemucs_6s.yaml
```

分離結果は `separated/<元ファイル名>/` 以下に保存される。