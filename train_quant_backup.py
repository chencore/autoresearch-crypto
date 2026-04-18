"""
加密货币量化策略训练脚本。
基于 autoresearch 架构，使用 Transformer 生成交易策略信号。

Usage:
    uv run python train_quant.py
"""

import os
import gc
import math
import time
from dataclasses import dataclass, field
from datetime import datetime

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "data", "crypto")
TOKENIZER_DIR = os.path.join(PROJECT_DIR, "tokenizer")

# 固定超参数（类比 prepare.py 常量）
MAX_SEQ_LEN = 256       # 时间窗口长度 (256 * 5min ≈ 21小时)
TIME_BUDGET = 300       # 训练时间预算（秒）
EVAL_STEPS = 200        # 评估步数
PREDICTION_HORIZON = 6  # 预测未来6根K线（30分钟，过滤单K噪声）
INITIAL_CAPITAL = 10000.0
COMMISSION = 0.001       # 0.1% 手续费
SLIPPAGE = 0.0005       # 0.05% 滑点
HOLD_THRESHOLD = 0.0003  # 收益率绝对值小于此值视为HOLD

# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def list_crypto_files():
    """列出所有加密货币数据文件"""
    if not os.path.exists(DATA_DIR):
        return []
    return [os.path.join(DATA_DIR, f) for f in os.listdir(DATA_DIR) if f.endswith(".parquet")]


def load_crypto_data(filepath):
    """加载单个 Parquet 文件"""
    import pyarrow.parquet as pq
    table = pq.read_table(filepath)
    return table.to_pandas()


class CryptoDataset:
    """加密货币数据集"""

    # 特征列名（与 prepare_crypto.py compute_features 输出一致）
    FEATURE_COLS = [
        "open", "high", "low", "close", "volume",
        "returns", "volatility", "rsi", "macd", "macd_signal",
        "macd_hist", "bb_upper", "bb_mid", "bb_lower", "atr", "volume_ratio"
    ]
    N_FEATURES = len(FEATURE_COLS)

    def __init__(self, filepaths, seq_len=MAX_SEQ_LEN, prediction_horizon=PREDICTION_HORIZON):
        self.seq_len = seq_len
        self.prediction_horizon = prediction_horizon

        # 合并所有数据文件
        import pandas as pd
        dfs = []
        for fp in filepaths:
            df = load_crypto_data(fp)
            dfs.append(df)
        self.data = pd.concat(dfs, ignore_index=True)

        # 排序并去除重复
        self.data = self.data.sort_values("timestamp").drop_duplicates()

        # 丢弃包含 NaN 的行（技术指标计算初期数据不足）
        self.data = self.data.dropna()

        # 归一化特征
        self._normalize()

        # 转为 numpy
        self.features = self.data[self.FEATURE_COLS].values.astype(np.float32)
        self.future_returns = self.data["close"].values.astype(np.float32)

    def _normalize(self):
        """Z-score 归一化特征"""
        for col in self.FEATURE_COLS:
            mean = self.data[col].mean()
            std = self.data[col].std()
            if std < 1e-8:
                std = 1.0
            self.data[col] = (self.data[col] - mean) / std

    def __len__(self):
        # 需要 seq_len 窗口 + prediction_horizon
        return max(0, len(self.data) - self.seq_len - self.prediction_horizon)

    def __getitem__(self, idx):
        # idx 是起始位置
        x = self.features[idx:idx + self.seq_len]
        # 未来收益率 = 未来 prediction_horizon 根K线的收盘价变化率
        future_start = idx + self.seq_len
        future_end = future_start + self.prediction_horizon
        current_price = self.future_returns[future_start - 1]
        future_price = self.future_returns[future_end - 1]
        fut_ret = (future_price - current_price) / current_price
        return torch.from_numpy(x), fut_ret


class CryptoDataLoader:
    """加密货币数据加载器"""

    def __init__(self, dataset, batch_size, split="train", val_ratio=0.1):
        self.dataset = dataset
        self.batch_size = batch_size
        self.split = split

        # 按时间顺序划分：最新的作为验证集
        n = len(dataset)
        val_size = max(1, int(n * val_ratio))
        if split == "train":
            self.indices = list(range(0, n - val_size))
        else:
            self.indices = list(range(n - val_size, n))

        self.pos = 0

    def __iter__(self):
        self.pos = 0
        # 打乱训练集
        if self.split == "train":
            import random
            random.shuffle(self.indices)
        return self

    def __next__(self):
        if self.pos >= len(self.indices):
            raise StopIteration

        # 收集一个 batch
        x_list = []
        y_list = []
        for _ in range(self.batch_size):
            if self.pos >= len(self.indices):
                break
            idx = self.indices[self.pos]
            x, y = self.dataset[idx]
            x_list.append(x)
            y_list.append(y)
            self.pos += 1

        x = torch.stack(x_list)
        y = torch.tensor(y_list, dtype=torch.float32)
        return x, y


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------

def norm(x):
    return F.rms_norm(x, (x.size(-1),))


def apply_rotary_emb(x, cos, sin):
    assert x.ndim == 4
    d = x.shape[-1] // 2
    x1, x2 = x[..., :d], x[..., d:]
    y1 = x1 * cos + x2 * sin
    y2 = x1 * (-sin) + x2 * cos
    return torch.cat([y1, y2], 3)


@dataclass
class StrategyConfig:
    seq_len: int = MAX_SEQ_LEN
    n_features: int = CryptoDataset.N_FEATURES
    n_embd: int = 256
    n_layer: int = 6
    n_head: int = 4
    n_kv_head: int = 4
    n_output: int = 3  # 卖出/持有/买入


class MarketEncoder(nn.Module):
    """数值特征 → 嵌入向量（替代 Tokenizer）"""
    def __init__(self, n_features, n_embd):
        super().__init__()
        self.projection = nn.Linear(n_features, n_embd, bias=False)
        # 可学习的 CLS token
        self.cls_token = nn.Parameter(torch.randn(n_embd))

    def forward(self, x):
        # x: (B, T, n_features)
        x = self.projection(x)  # (B, T, n_embd)
        cls = self.cls_token.unsqueeze(0).unsqueeze(0)  # (1, 1, n_embd)
        cls = cls.expand(x.size(0), -1, -1)
        x = torch.cat([cls, x], dim=1)  # (B, T+1, n_embd)
        return x


class CausalSelfAttention(nn.Module):
    """因果自注意力（复用原架构）"""
    def __init__(self, config, layer_idx):
        super().__init__()
        self.n_head = config.n_head
        self.n_kv_head = config.n_kv_head
        self.n_embd = config.n_embd
        self.head_dim = self.n_embd // self.n_head
        self.c_q = nn.Linear(self.n_embd, self.n_head * self.head_dim, bias=False)
        self.c_k = nn.Linear(self.n_embd, self.n_kv_head * self.head_dim, bias=False)
        self.c_v = nn.Linear(self.n_embd, self.n_kv_head * self.head_dim, bias=False)
        self.c_proj = nn.Linear(self.n_embd, self.n_embd, bias=False)

    def forward(self, x, cos_sin, window_size):
        B, T, C = x.size()
        q = self.c_q(x).view(B, T, self.n_head, self.head_dim)
        k = self.c_k(x).view(B, T, self.n_kv_head, self.head_dim)
        v = self.c_v(x).view(B, T, self.n_kv_head, self.head_dim)

        cos, sin = cos_sin
        q, k = apply_rotary_emb(q, cos, sin), apply_rotary_emb(k, cos, sin)
        q, k = norm(q), norm(k)

        # 简化的注意力（不用 FA3，用标准 attention）
        q = q.transpose(1, 2)  # (B, n_head, T, head_dim)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)

        scale = self.head_dim ** -0.5
        attn = torch.matmul(q, k.transpose(-2, -1)) * scale
        attn = F.pad(attn, (0, T - attn.size(-1)), "constant", float("-inf"))
        attn = F.softmax(attn, dim=-1)
        y = torch.matmul(attn, v)
        y = y.transpose(1, 2).contiguous().view(B, T, -1)
        y = self.c_proj(y)
        return y


class MLP(nn.Module):
    """前馈网络（复用原架构，使用 ReLU²）"""
    def __init__(self, config):
        super().__init__()
        self.c_fc = nn.Linear(config.n_embd, 4 * config.n_embd, bias=False)
        self.c_proj = nn.Linear(4 * config.n_embd, config.n_embd, bias=False)

    def forward(self, x):
        x = self.c_fc(x)
        x = F.relu(x).square()
        x = self.c_proj(x)
        return x


class Block(nn.Module):
    """Transformer 块（Pre-Norm + Dropout）"""
    def __init__(self, config, layer_idx):
        super().__init__()
        self.attn = CausalSelfAttention(config, layer_idx)
        self.mlp = MLP(config)
        self.dropout = nn.Dropout(0.1)

    def forward(self, x, cos_sin, window_size):
        x = x + self.dropout(self.attn(norm(x), cos_sin, window_size))
        x = x + self.dropout(self.mlp(norm(x)))
        return x


class StrategyTransformer(nn.Module):
    """策略生成 Transformer（替代 GPT）"""
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.window_sizes = self._compute_window_sizes(config)

        self.encoder = MarketEncoder(config.n_features, config.n_embd)
        self.blocks = nn.ModuleList([Block(config, i) for i in range(config.n_layer)])
        self.norm = nn.RMSNorm(config.n_embd)
        self.signal_head = nn.Linear(config.n_embd, config.n_output, bias=False)

        # 预计算 RoPE
        self.rotary_seq_len = config.seq_len * 2
        cos, sin = self._precompute_rotary_embeddings(self.rotary_seq_len, config.n_embd // config.n_head)
        self.register_buffer("cos", cos, persistent=False)
        self.register_buffer("sin", sin, persistent=False)

    def _compute_window_sizes(self, config):
        pattern = "SSSSSS"  # 全用短窗口
        long_window = config.seq_len
        short_window = long_window // 2
        char_to_window = {"L": (long_window, 0), "S": (short_window, 0)}
        window_sizes = []
        for i in range(config.n_layer):
            char = pattern[i % len(pattern)]
            window_sizes.append(char_to_window[char])
        return window_sizes

    def _precompute_rotary_embeddings(self, seq_len, head_dim, base=10000, device=None):
        if device is None:
            device = self.encoder.projection.weight.device
        channel_range = torch.arange(0, head_dim, 2, dtype=torch.float32, device=device)
        inv_freq = 1.0 / (base ** (channel_range / head_dim))
        t = torch.arange(seq_len, dtype=torch.float32, device=device)
        freqs = torch.outer(t, inv_freq)
        cos, sin = freqs.cos(), freqs.sin()
        cos, sin = cos.bfloat16(), sin.bfloat16()
        cos, sin = cos[None, :, None, :], sin[None, :, None, :]
        return cos, sin

    def forward(self, x):
        # x: (B, T, n_features)
        x = self.encoder(x)  # (B, T+1, n_embd)
        T_plus_1 = x.size(1)
        cos_sin = self.cos[:, :T_plus_1], self.sin[:, :T_plus_1]

        for i, block in enumerate(self.blocks):
            x = block(x, cos_sin, self.window_sizes[i])

        x = self.norm(x)
        # 只用 CLS token 预测
        cls_output = x[:, 0]  # (B, n_embd)
        logits = self.signal_head(cls_output)  # (B, 3)
        return logits  # (卖出, 持有, 买入)


# ---------------------------------------------------------------------------
# 评估器
# ---------------------------------------------------------------------------

class StrategyEvaluator:
    """策略绩效评估器"""

    def __init__(self, initial_capital=INITIAL_CAPITAL, commission=COMMISSION, slippage=SLIPPAGE):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def simulate(self, signals, future_returns):
        """
        模拟交易（只做多/空仓，不做空）

        Args:
            signals: (N,) tensor, 0=卖出(空仓), 1=持有, 2=买入(多头)
            future_returns: (N,) tensor, 未来收益率

        Returns:
            equity_curve: 权益曲线
            trades: 交易记录
        """
        signals = signals.cpu().numpy()
        future_returns = future_returns.cpu().numpy()

        # 从收益率重建相对价格序列
        prices = [1.0]
        for ret in future_returns:
            prices.append(prices[-1] * (1 + ret))
        prices = np.array(prices)

        capital = self.initial_capital
        shares = 0.0
        position = 0  # 0=空仓, 1=多头
        equity = []
        trades = []
        entry_cost_basis = 0.0

        for i in range(len(signals)):
            signal = signals[i]
            price = prices[i]

            # 目标仓位
            if signal == 2:
                target_pos = 1
            elif signal == 0:
                target_pos = 0
            else:
                target_pos = position

            # 执行换仓
            if target_pos != position:
                if target_pos == 1 and position == 0:
                    exec_price = price * (1 + self.slippage)
                    shares = capital * (1 - self.commission) / exec_price
                    entry_cost_basis = capital
                    capital = 0.0
                    trades.append({"type": "buy", "step": i})
                    position = 1
                elif target_pos == 0 and position == 1:
                    exec_price = price * (1 - self.slippage)
                    gross = shares * exec_price
                    cost = gross * self.commission
                    capital = gross - cost
                    pnl = capital - entry_cost_basis
                    trades.append({"type": "sell", "step": i, "pnl": float(pnl)})
                    shares = 0.0
                    position = 0

            current_equity = capital + shares * price
            equity.append(current_equity)

        # 最终强制平仓
        if position == 1:
            exec_price = prices[-1] * (1 - self.slippage)
            gross = shares * exec_price
            cost = gross * self.commission
            capital = gross - cost
            pnl = capital - entry_cost_basis
            trades.append({"type": "sell_final", "step": len(signals) - 1, "pnl": float(pnl)})
            equity[-1] = capital
            position = 0
            shares = 0.0

        return np.array(equity), trades

    def compute_metrics(self, equity_curve, trades):
        """计算绩效指标"""
        equity = equity_curve
        returns = np.diff(equity) / equity[:-1]

        # 总收益率
        total_return = (equity[-1] / equity[0]) - 1

        # 年化收益率（假设每5分钟一根K线，每天288根）
        n_steps = len(equity)
        years = n_steps * 5 / (288 * 365)
        if years < 0.01:
            years = 0.01
        annualized_return = (1 + total_return) ** (1 / years) - 1
        # 限制极端值，防止评分异常
        annualized_return = max(-10.0, min(10.0, annualized_return))

        # 年化波动率
        annualized_vol = np.std(returns) * math.sqrt(288 * 365) if len(returns) > 0 else 0

        # 夏普比率
        sharpe = annualized_return / annualized_vol if annualized_vol > 0 else 0

        # 最大回撤
        peak = equity[0]
        max_drawdown = 0
        for e in equity:
            if e > peak:
                peak = e
            dd = (e - peak) / peak
            if dd < max_drawdown:
                max_drawdown = dd

        # 胜率（基于实际交易盈亏）
        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        total_trades = len(trade_pnls)
        winning_trades = len([t for t in trade_pnls if t["pnl"] > 0])
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.5

        return {
            "total_return": total_return,
            "annualized_return": annualized_return,
            "annualized_vol": annualized_vol,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
        }

    def evaluate(self, model, data_loader, device):
        """在数据加载器上评估模型"""
        model.eval()
        all_signals = []
        all_returns = []

        with torch.no_grad():
            for step, (x, y) in enumerate(data_loader):
                if step >= EVAL_STEPS:
                    break
                x = x.to(device)
                logits = model(x)
                # 信号: 0=卖出(空仓), 1=持有, 2=买入(多头)
                signals = torch.argmax(logits, dim=-1)
                all_signals.append(signals.cpu())
                all_returns.append(y)

        all_signals = torch.cat(all_signals)
        all_returns = torch.cat(all_returns)

        equity, trades = self.simulate(all_signals, all_returns)
        metrics = self.compute_metrics(equity, trades)

        # 综合评分（越高越好）
        score = (
            max(0, metrics["sharpe_ratio"]) * 0.4 +
            max(0, metrics["total_return"]) * 0.3 +
            metrics["win_rate"] * 0.1 +
            (1 + metrics["max_drawdown"]) * 0.1 +   # 回撤越小越好
            min(1.0, len(trades) / 20.0) * 0.1      # 有适当交易次数加分
        )

        return score, metrics


# ---------------------------------------------------------------------------
# 超参数（可直接修改）
# ---------------------------------------------------------------------------

# 模型架构（针对中等数据集调优：120天约34K条）
ASPECT_RATIO = 24        # 模型维度 = depth * ASPECT_RATIO (3*24=72)
HEAD_DIM = 48            # 注意力头维度（增加表达能力）

# 优化
TOTAL_BATCH_SIZE = 192   # 总批量大小（增加稳定性）
DEVICE_BATCH_SIZE = 48   # 设备批量大小
LEARNING_RATE = 0.0005   # 学习率（适中）
WEIGHT_DECAY = 0.01      # 权重衰减（正则化）
WARMUP_RATIO = 0.15      # 预热比例
WARMDOWN_RATIO = 0.35    # 冷却比例

# 模型规模
DEPTH = 3                # Transformer 层数（增加容量以学习择时模式）


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

def main():
    t_start = time.time()
    torch.manual_seed(42)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("=" * 60)
    print("加密货币量化策略训练")
    print("=" * 60)

    # 检查数据
    data_files = list_crypto_files()
    if not data_files:
        print("错误: 未找到数据文件。请先运行 python prepare_crypto.py")
        return

    print(f"找到 {len(data_files)} 个数据文件")
    for f in data_files:
        print(f"  {os.path.basename(f)}")

    # 创建数据集
    print("\n加载数据...")
    dataset = CryptoDataset(data_files)
    print(f"数据集大小: {len(dataset)} 个样本")
    print(f"特征数量: {dataset.N_FEATURES}")

    # 创建模型
    base_dim = DEPTH * ASPECT_RATIO
    model_dim = ((base_dim + HEAD_DIM - 1) // HEAD_DIM) * HEAD_DIM
    num_heads = model_dim // HEAD_DIM

    config = StrategyConfig(
        seq_len=MAX_SEQ_LEN,
        n_features=dataset.N_FEATURES,
        n_embd=model_dim,
        n_layer=DEPTH,
        n_head=num_heads,
        n_kv_head=num_heads,
    )

    print(f"\n模型配置:")
    print(f"  depth: {config.n_layer}")
    print(f"  n_embd: {config.n_embd}")
    print(f"  n_head: {config.n_head}")
    print(f"  seq_len: {config.seq_len}")

    model = StrategyTransformer(config).to(device)
    num_params = sum(p.numel() for p in model.parameters())
    print(f"模型参数量: {num_params / 1e6:.1f}M")

    # 优化器
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=LEARNING_RATE,
        weight_decay=WEIGHT_DECAY,
        betas=(0.9, 0.95),
    )

    # 数据加载器
    train_loader = CryptoDataLoader(dataset, DEVICE_BATCH_SIZE, split="train")
    val_loader = CryptoDataLoader(dataset, DEVICE_BATCH_SIZE, split="val")

    # 评估器
    evaluator = StrategyEvaluator()

    # 学习率调度
    total_steps = TIME_BUDGET // 2  # 估算总步数

    def get_lr_multiplier(step):
        # WARMUP_RATIO warmup, then constant, then WARMDOWN_RATIO warmdown
        warmup_steps = int(total_steps * WARMUP_RATIO)
        constant_steps = int(total_steps * (1 - WARMUP_RATIO - WARMDOWN_RATIO))
        warmdown_start = warmup_steps + constant_steps

        if step < warmup_steps:
            # Linear warmup
            return step / warmup_steps if warmup_steps > 0 else 1.0
        elif step < warmdown_start:
            # Constant
            return 1.0
        else:
            # Linear warmdown
            warmdown_progress = (step - warmdown_start) / (total_steps - warmdown_start)
            return max(0.0, 1.0 - warmdown_progress)

    # 训练循环
    print(f"\n开始训练 (时间预算: {TIME_BUDGET}s)")

    model.train()
    step = 0
    total_time = 0
    smooth_loss = 0

    while True:
        torch.cuda.synchronize() if torch.cuda.is_available() else None
        t0 = time.time()

        # 前向传播
        for _ in range(TOTAL_BATCH_SIZE // DEVICE_BATCH_SIZE):
            try:
                x, y = next(train_loader)
            except StopIteration:
                train_loader.__iter__()
                x, y = next(train_loader)

            x, y = x.to(device), y.to(device)

            optimizer.zero_grad()

            logits = model(x)  # (B, 3)
            # 目标: 根据未来收益率选择信号
            # |y| < HOLD_THRESHOLD -> HOLD(1)
            # y >= HOLD_THRESHOLD -> BUY(2)
            # y <= -HOLD_THRESHOLD -> SELL(0)
            targets = torch.where(
                y >= HOLD_THRESHOLD, 2,
                torch.where(y <= -HOLD_THRESHOLD, 0, 1)
            )

            loss = F.cross_entropy(logits, targets)

            loss.backward()
            optimizer.step()

        # 学习率调度
        lr_mult = get_lr_multiplier(step)
        for param_group in optimizer.param_groups:
            param_group["lr"] = LEARNING_RATE * lr_mult

        torch.cuda.synchronize() if torch.cuda.is_available() else None
        dt = time.time() - t0

        if step > 0:
            total_time += dt

        loss_val = loss.item()
        smooth_loss = 0.9 * smooth_loss + 0.1 * loss_val

        # 日志
        if step % 10 == 0:
            pct = 100 * total_time / TIME_BUDGET if total_time > 0 else 0
            remaining = max(0, TIME_BUDGET - total_time)
            print(f"\rstep {step:04d} ({pct:.1f}%) | loss: {smooth_loss:.4f} | lr: {lr_mult:.3f} | dt: {dt*1000:.0f}ms | 剩余: {remaining:.0f}s    ", end="", flush=True)

        step += 1

        # 时间预算耗尽
        if step > 5 and total_time >= TIME_BUDGET:
            break

    print()  # newline

    # 最终评估
    # 保存模型和配置
    checkpoint_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, "quant_model.pt")

    # 收集归一化统计量
    norm_stats = {}
    for col in dataset.FEATURE_COLS:
        mean = dataset.data[col].mean()
        std = dataset.data[col].std()
        if std < 1e-8:
            std = 1.0
        norm_stats[col] = {"mean": float(mean), "std": float(std)}

    checkpoint = {
        "model_state_dict": model.state_dict(),
        "config": config,
        "norm_stats": norm_stats,
        "hyperparams": {
            "MAX_SEQ_LEN": MAX_SEQ_LEN,
            "PREDICTION_HORIZON": PREDICTION_HORIZON,
            "DEPTH": DEPTH,
            "ASPECT_RATIO": ASPECT_RATIO,
            "HEAD_DIM": HEAD_DIM,
        },
        "metrics": {
            "score": score if 'score' in locals() else None,
            "step": step,
        }
    }
    torch.save(checkpoint, checkpoint_path)
    print(f"\n模型已保存: {checkpoint_path}")

    print("\n评估中...")
    score, metrics = evaluator.evaluate(model, val_loader, device)

    print("\n" + "=" * 60)
    print("结果")
    print("=" * 60)
    print(f"综合评分:     {score:.6f}")
    print(f"夏普比率:     {metrics['sharpe_ratio']:.4f}")
    print(f"总收益率:     {metrics['total_return']*100:.2f}%")
    print(f"年化收益率:   {metrics['annualized_return']*100:.2f}%")
    print(f"年化波动率:   {metrics['annualized_vol']*100:.2f}%")
    print(f"最大回撤:     {metrics['max_drawdown']*100:.2f}%")
    print(f"胜率:         {metrics['win_rate']*100:.1f}%")
    print(f"训练步数:     {step}")
    print(f"总耗时:       {time.time() - t_start:.1f}s")

    return score, metrics


if __name__ == "__main__":
    main()
