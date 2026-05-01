"""
Nado DEX live trading entry point.

Usage:
    uv run python scripts/live_nado.py --ticker ETH --interval 5m --capital 100
    uv run python scripts/live_nado.py --ticker ETH --interval 5m --capital 100 \\
        --checkpoint checkpoints/hybrid_mm_eth60d.pt
"""
import sys
import os

# Ensure project root is on path for backward compat imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    # Delegate to the existing live_nado_quant.py (backward compat)
    import runpy
    runpy.run_path(
        os.path.join(os.path.dirname(__file__), "..", "live_nado_quant.py"),
        run_name="__main__",
    )
