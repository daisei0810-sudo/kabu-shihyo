"""マスタデータ(銘柄・指標・テーマ)を config/*.csv から読み込むレジストリ層。

Investment OS Layer1(Vision/銘柄マスタ)・Layer3(Indicator Dictionary)の実装。
正本は CSV ファイル側であり、コード内の `INSTRUMENTS`/`INDICATORS` 静的リストは
廃止した。銘柄・指標の追加はこの下の CSV へ1行追加するだけでよい。

依存方向: src.registry → src.config (逆はNG。config.py 側は起動時に一度だけ
このパッケージのローダーを呼び出し、結果を `INSTRUMENTS`/`INDICATORS` に束縛する)。
"""
