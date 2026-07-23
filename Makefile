.PHONY: separate help

help:
	@echo "利用可能なコマンド:"
	@echo "  make separate INPUT=path/to/song.mp3  - 音源分離アプリ(separate.py)をmodal run経由で実行"

separate:
	uv run modal run separate.py --input-path "$(INPUT)"

%:
	@:
