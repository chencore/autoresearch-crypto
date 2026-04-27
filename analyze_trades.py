"""
策略交易数据分析脚本
加载最优参数，运行回测，输出详细的交易统计
"""

import os
import math
import numpy as np
import pandas as pd
import torch

from train_quant import BollingerStrategy, StrategyEvaluator, load_crypto_data, DATA_DIR

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def analyze_trades():
    # 加载最优参数
    checkpoint_path = os.path.join(PROJECT_DIR, "checkpoints", "quant_model.pt")
    if not os.path.exists(checkpoint_path):
        print("错误: 未找到检查点文件")
        return

    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    params = ckpt["params"]
    print("=" * 70)
    print("策略交易数据分析")
    print("=" * 70)
    print(f"检查点来源币种: {ckpt.get('all_results', [{}])[0].get('symbol', 'unknown')}")
    print(f"检查点评分: {ckpt.get('score', 0):.4f}")
    print()
    print("参数配置:")
    for k, v in sorted(params.items()):
        print(f"  {k}: {v}")
    print()

    # 加载数据
    data_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]
    if not data_files:
        print("错误: 未找到数据文件")
        return

    # 优先使用检查点中的币种
    symbol = ckpt.get("all_results", [{}])[0].get("symbol", "ETH")
    target_file = None
    for f in data_files:
        if symbol in f:
            target_file = os.path.join(DATA_DIR, f)
            break
    if not target_file:
        target_file = os.path.join(DATA_DIR, data_files[0])
        symbol = data_files[0].replace("_5m.parquet", "").replace("_1m.parquet", "")

    df = load_crypto_data(target_file)
    df = df.sort_values("timestamp").drop_duplicates().reset_index(drop=True)
    print(f"分析币种: {symbol}")
    print(f"数据量: {len(df)} 条K线 ({len(df)*5/60:.1f} 小时)")
    print(f"价格范围: {df['close'].min():.2f} - {df['close'].max():.2f}")
    print()

    # 运行策略
    strategy = BollingerStrategy(**params)
    signals = strategy.generate_signals(df, enable_short=True)
    prices = df["close"].values

    window = params.get("window", 20)
    valid_signals = signals[window*2:]
    valid_prices = prices[window*2:]
    valid_df = df.iloc[window*2:].reset_index(drop=True)

    evaluator = StrategyEvaluator()
    equity, trades = evaluator.simulate(valid_signals, valid_prices, valid_df)
    metrics = evaluator.compute_metrics(equity, trades)

    # 交易记录分析
    trade_pnls = []
    long_trades = []
    short_trades = []

    entry_info = {}
    for t in trades:
        if t["type"] in ("buy", "sell_short"):
            entry_info[t["type"]] = {"step": t["step"], "price": valid_prices[t["step"]]}
        elif t["type"] in ("sell", "sell_final", "buy_cover") and "pnl" in t:
            side = "long" if t["type"] in ("sell", "sell_final") else "short"
            entry_type = "buy" if side == "long" else "sell_short"
            ei = entry_info.get(entry_type, {})
            record = {
                "side": side,
                "entry_step": ei.get("step", 0),
                "exit_step": t["step"],
                "entry_price": ei.get("price", 0),
                "exit_price": valid_prices[t["step"]],
                "pnl": t["pnl"],
                "hold_bars": t["step"] - ei.get("step", 0),
            }
            trade_pnls.append(record)
            if side == "long":
                long_trades.append(record)
            else:
                short_trades.append(record)

    if not trade_pnls:
        print("警告: 没有找到任何完成的交易")
        return

    pnls = np.array([t["pnl"] for t in trade_pnls])
    hold_times = np.array([t["hold_bars"] for t in trade_pnls])

    # ============= 总体统计 =============
    print("=" * 70)
    print("总体绩效")
    print("=" * 70)
    print(f"总收益率:       {metrics['total_return']*100:.2f}%")
    print(f"年化收益率:     {metrics['annualized_return']*100:.2f}%")
    print(f"夏普比率:       {metrics['sharpe_ratio']:.4f}")
    print(f"最大回撤:       {metrics['max_drawdown']*100:.2f}%")
    print(f"胜率:           {metrics['win_rate']*100:.1f}%")
    print()

    # ============= 交易统计 =============
    print("=" * 70)
    print("交易统计")
    print("=" * 70)
    print(f"总交易次数:     {len(trade_pnls)}")
    print(f"做多次数:       {len(long_trades)}")
    print(f"做空次数:       {len(short_trades)}")
    print()

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    breakeven = pnls[pnls == 0]

    print(f"盈利交易:       {len(wins)} ({len(wins)/len(trade_pnls)*100:.1f}%)")
    print(f"亏损交易:       {len(losses)} ({len(losses)/len(trade_pnls)*100:.1f}%)")
    print(f"盈亏平衡:       {len(breakeven)}")
    print()

    print(f"总盈亏:         {pnls.sum():.2f} USDT")
    print(f"平均盈亏:       {pnls.mean():.2f} USDT")
    print(f"盈亏中位数:     {np.median(pnls):.2f} USDT")
    print(f"最大单笔盈利:   {pnls.max():.2f} USDT")
    print(f"最大单笔亏损:   {pnls.min():.2f} USDT")
    print(f"盈亏比:         {abs(wins.mean()/losses.mean()):.2f}" if len(losses) > 0 else "盈亏比: N/A")
    print()

    # ============= 持仓时间分析 =============
    print("=" * 70)
    print("持仓时间分析（K线数）")
    print("=" * 70)
    print(f"平均持仓:       {hold_times.mean():.1f} 根K线 ({hold_times.mean()*5:.0f} 分钟)")
    print(f"中位数持仓:     {np.median(hold_times):.0f} 根K线")
    print(f"最短持仓:       {hold_times.min()} 根K线")
    print(f"最长持仓:       {hold_times.max()} 根K线")
    print()

    # 按盈亏分组看持仓时间
    if len(wins) > 0:
        win_holds = [t["hold_bars"] for t in trade_pnls if t["pnl"] > 0]
        print(f"盈利交易平均持仓: {np.mean(win_holds):.1f} 根K线")
    if len(losses) > 0:
        loss_holds = [t["hold_bars"] for t in trade_pnls if t["pnl"] < 0]
        print(f"亏损交易平均持仓: {np.mean(loss_holds):.1f} 根K线")
    print()

    # ============= 连续盈亏分析 =============
    print("=" * 70)
    print("连续盈亏分析")
    print("=" * 70)
    streaks = []
    current_streak = 1
    current_type = "win" if pnls[0] > 0 else "loss"
    for i in range(1, len(pnls)):
        t = "win" if pnls[i] > 0 else "loss"
        if t == current_type:
            current_streak += 1
        else:
            streaks.append((current_type, current_streak))
            current_type = t
            current_streak = 1
    streaks.append((current_type, current_streak))

    win_streaks = [s[1] for s in streaks if s[0] == "win"]
    loss_streaks = [s[1] for s in streaks if s[0] == "loss"]

    if win_streaks:
        print(f"最长连续盈利:   {max(win_streaks)} 笔")
        print(f"平均连续盈利:   {np.mean(win_streaks):.1f} 笔")
    if loss_streaks:
        print(f"最长连续亏损:   {max(loss_streaks)} 笔")
        print(f"平均连续亏损:   {np.mean(loss_streaks):.1f} 笔")
    print()

    # ============= 多空分别统计 =============
    if long_trades:
        print("=" * 70)
        print("做多交易统计")
        print("=" * 70)
        lpnls = np.array([t["pnl"] for t in long_trades])
        print(f"次数: {len(long_trades)}, 胜率: {np.sum(lpnls>0)/len(long_trades)*100:.1f}%")
        print(f"总盈亏: {lpnls.sum():.2f}, 平均: {lpnls.mean():.2f}")
        print(f"最大盈: {lpnls.max():.2f}, 最大亏: {lpnls.min():.2f}")
        print()

    if short_trades:
        print("=" * 70)
        print("做空交易统计")
        print("=" * 70)
        spnls = np.array([t["pnl"] for t in short_trades])
        print(f"次数: {len(short_trades)}, 胜率: {np.sum(spnls>0)/len(short_trades)*100:.1f}%")
        print(f"总盈亏: {spnls.sum():.2f}, 平均: {spnls.mean():.2f}")
        print(f"最大盈: {spnls.max():.2f}, 最大亏: {spnls.min():.2f}")
        print()

    # ============= 资金曲线分析 =============
    print("=" * 70)
    print("资金曲线分析")
    print("=" * 70)
    returns = np.diff(equity) / equity[:-1]
    print(f"最终权益:       {equity[-1]:.2f} USDT")
    print(f"权益峰值:       {equity.max():.2f} USDT")
    print(f"权益谷值:       {equity.min():.2f} USDT")
    print(f"平均日收益:     {np.mean(returns)*100:.3f}%")
    print(f"收益波动:       {np.std(returns)*100:.3f}%")
    print()

    # 回撤分析
    peak = equity[0]
    drawdowns = []
    for e in equity:
        if e > peak:
            peak = e
        dd = (e - peak) / peak
        drawdowns.append(dd)
    drawdowns = np.array(drawdowns)

    print(f"回撤 < -5% 的时间:  {np.sum(drawdowns < -0.05)} 根K线 ({np.sum(drawdowns < -0.05)/len(drawdowns)*100:.1f}%)")
    print(f"回撤 < -10% 的时间: {np.sum(drawdowns < -0.10)} 根K线 ({np.sum(drawdowns < -0.10)/len(drawdowns)*100:.1f}%)")
    print(f"回撤 < -20% 的时间: {np.sum(drawdowns < -0.20)} 根K线 ({np.sum(drawdowns < -0.20)/len(drawdowns)*100:.1f}%)")
    print()

    # ============= 月度/周度收益分析 =============
    print("=" * 70)
    print("周期收益分布 (按每288根K线 ≈ 1天)")
    print("=" * 70)
    daily_returns = []
    for i in range(0, len(equity), 288):
        end = min(i + 288, len(equity) - 1)
        if end > i:
            r = (equity[end] - equity[i]) / equity[i]
            daily_returns.append(r)

    if daily_returns:
        dr = np.array(daily_returns)
        print(f"正收益天数:     {np.sum(dr > 0)}/{len(dr)} ({np.sum(dr>0)/len(dr)*100:.1f}%)")
        print(f"平均日收益:     {dr.mean()*100:.2f}%")
        print(f"最佳日收益:     {dr.max()*100:.2f}%")
        print(f"最差日收益:     {dr.min()*100:.2f}%")
        print(f"收益标准差:     {dr.std()*100:.2f}%")
    print()

    # ============= 交易列表（最近10笔） =============
    print("=" * 70)
    print("最近10笔交易明细")
    print("=" * 70)
    print(f"{'#':<3} {'方向':<5} {'入场价':<10} {'出场价':<10} {'盈亏':<10} {'持仓K线':<8}")
    print("-" * 60)
    for i, t in enumerate(trade_pnls[-10:]):
        print(f"{i+1:<3} {t['side']:<5} {t['entry_price']:<10.2f} {t['exit_price']:<10.2f} {t['pnl']:<10.2f} {t['hold_bars']:<8}")
    print()

    # ============= 保存详细交易记录 =============
    output_path = os.path.join(PROJECT_DIR, "trade_analysis.csv")
    trade_df = pd.DataFrame(trade_pnls)
    trade_df.to_csv(output_path, index=False)
    print(f"详细交易记录已保存: {output_path}")


if __name__ == "__main__":
    analyze_trades()
