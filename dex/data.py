"""Data loading utilities for cryptocurrency OHLCV data."""

import os
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pyarrow.parquet as pq

from dex.config import DATA_DIR


def list_crypto_files(data_dir: Optional[str] = None) -> List[str]:
    """List all cryptocurrency parquet data files.

    Prefers files with day-count tags (e.g. ``ETHUSDT_5m_60d.parquet``)
    over older untagged files to avoid double-loading the same symbol.

    Args:
        data_dir: Directory containing parquet files. Defaults to
                  ``dex.config.DATA_DIR``.

    Returns:
        Sorted list of absolute file paths.
    """
    directory = Path(data_dir) if data_dir else DATA_DIR
    if not directory.exists():
        return []

    all_files = [f for f in os.listdir(str(directory)) if f.endswith(".parquet")]

    # Group by (symbol, interval) and prefer tagged files
    grouped: dict = {}
    for fname in all_files:
        parts = fname.replace(".parquet", "").split("_")
        if len(parts) >= 2:
            key = (parts[0], parts[1])  # (symbol, interval)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(fname)

    selected: List[str] = []
    for key, fnames in grouped.items():
        tagged = [
            f for f in fnames
            if len(f.replace(".parquet", "").split("_")) >= 3
        ]
        if tagged:
            selected.append(sorted(tagged)[-1])  # largest day count
        else:
            selected.append(fnames[0])

    return [str(directory / f) for f in selected]


def load_crypto_data(filepath: str) -> pd.DataFrame:
    """Load a single Parquet file into a pandas DataFrame.

    Args:
        filepath: Absolute or relative path to the .parquet file.

    Returns:
        DataFrame with OHLCV columns (and computed features if present).
    """
    table = pq.read_table(filepath)
    df = table.to_pandas()

    # Ensure numeric columns are float
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)

    return df


def load_all_crypto(data_dir: Optional[str] = None) -> pd.DataFrame:
    """Load and concatenate all crypto data files.

    Args:
        data_dir: Directory containing parquet files.

    Returns:
        Concatenated DataFrame with all symbols.
    """
    files = list_crypto_files(data_dir)
    if not files:
        raise FileNotFoundError(f"No parquet files found in {data_dir or DATA_DIR}")

    dfs = []
    for f in files:
        df = load_crypto_data(f)
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True)
