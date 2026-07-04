import re

from dex.config import DATA_DIR

from app.schemas.backtest import SymbolInfo

FILENAME_RE = re.compile(
    r"^(?P<symbol>.+?)_(?P<interval>\d+[mh])_(?P<days>\d+)d\.parquet$"
)


def list_symbols() -> list[SymbolInfo]:
    if not DATA_DIR.exists():
        return []
    results: list[SymbolInfo] = []
    for path in sorted(DATA_DIR.glob("*.parquet")):
        m = FILENAME_RE.match(path.name)
        if not m:
            continue
        results.append(
            SymbolInfo(
                symbol=m.group("symbol"),
                interval=m.group("interval"),
                days=int(m.group("days")),
                file=path.name,
            )
        )
    results.sort(key=lambda s: s.symbol)
    return results
