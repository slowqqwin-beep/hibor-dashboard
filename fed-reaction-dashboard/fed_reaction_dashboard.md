# Fed Reaction Dashboard v2

**2026-06-23 13:05:58 CST** | **2026-06-23 05:05:58 UTC** | Futu + yfinance

## 0. State Machine: **⏸ OBSERVE** (Day 7)

> No trigger B=0 C=0 E=1

> **To upgrade**: B>=3 (now 0/4)

## Driver Attribution

**混合信号**

> 13W-0.5bp(0.4σ) / 5Y+2.3bp(0.5σ) / 10Y+2.4bp(0.6σ) / 30Y+2.4bp

## Gates

| Gate | Status | Detail |
|------|--------|--------|
| vix_contango | ✅ | VIX/VIX3M=0.824 ok |
| casc | ✅ | CASC ok |
| credit | ✅ | HYG z=+1.55(20d:up) LQD z=+0.87: ok |

## 1. UST Yields

| Tenor | Latest | Daily (bp) | 5D (bp) | 20D vol (bp) |
|-------|--------|-----------|---------|-------------|
| 13W | 3.618% | -0.5 | -1.0 | 1.2 |
| 5Y | 4.213% | +2.3 | -6.8 | 4.56 |
| 10Y | 4.487% | +2.4 | -6.5 | 4.03 |
| 30Y | 4.975% | +2.4 | -4.9 | 3.08 |

## 2. ETF Snapshot (Futu)

| Ticker | Price | Daily | 5D | Signal |
|--------|-------|-------|-----|--------|
| SHY | $81.91 | -0.10% | -0.24% | 2Y proxy |
| IEF | $94.0 | -0.38% | -0.30% | 10Y proxy |
| TLT | $86.09 | -0.76% | +0.43% | long-end |
| UUP | $28.36 | +0.21% | +1.39% | USD |
| GLD | $384.59 | -0.65% | -3.02% | Gold |
| QQQ | $737.95 | -0.25% | -0.70% | Nasdaq |
| SPY | $744.39 | -0.31% | -1.13% | S&P500 |
| IWM | $298.18 | +0.88% | +1.20% | Russell |
| HYG | $79.94 | -0.09% | -0.12% | HY credit |
| LQD | $108.78 | -0.27% | -0.19% | IG credit |
| VXX | $22.52 | -1.23% | -0.18% | Volatility |
| CL | $88.67 | -0.91% | -2.11% | WTI |

## 3. VIX

**16.91** (daily +0.51)

## 4. Score Modules

| Module | Score | Max | Strength | Details |
|--------|-------|-----|----------|--------|
| A. Hawkish | 2 | 4 | SHY_down=2Y_up -0.10% z=0.9; DXY_up +0.21% z=0.7; — 以下未达阈值 —; Gold_down -0.65% (thresh -0.30%, z=0.4); Nasdaq_weak -0.25% (thresh -0.50%, z=0.1) |
| B. Dovish | 0 | 4 | — 以下未达阈值 —; SHY_up=2Y_down -0.10% (thresh +0.05%, z=0.9); DXY_down +0.21% (thresh -0.05%, z=0.7); Gold_up -0.65% (thresh +0.30%, z=0.4); Nasdaq_strong -0.25% (thresh +0.50%, z=0.1) |
| C. Liquidity | 0 | 3 | VIX=16.91 (thresh >18); HYG z=+1.55 LQD z=+0.87 spread=+0.68 (credit neutral); IWM-SPY=+1.19% (thresh <-0.30%) |
| D. Inflation | 1 | 4 | curve: 5Y_5d=-6.8 10Y_5d=-6.5 30Y_5d=-4.9 (no bear-steepen/bear-flatten); 30Y_up TLT-0.76%; WTI-0.91% (thresh >+2.0%) |
| E. Growth | 1 | 3 | SHY-0.10% IEF-0.38% (need both >+0.05%); IWM-SPY=+1.19% (thresh <-0.30%); QQQ-IWM divergence -1.13% |

## 6. Curve Signals

- **BAD**: Inflation/hawkish pressure

## 7. 2Y/10Y Interpretation

> Note: 2Y proxy=SHY, 10Y proxy=IEF; ETF up = yield down
- **2Y proxy(SHY)**: yield_up(hawkish) (-0.10%)
- **10Y proxy(IEF)**: yield_up (-0.38%)
- **30Y(TLT)**: yield_up (-0.76%)
- **10Y=4.487% < 4.6%**: manageable

## 8. ABCD Cross-Validation

| This Tool | ABCD Reading | Match? |
|-----------|-------------|--------|
| A Hawkish 2/4 | 🔴 长端贴现率/真实利率压力已很高，通胀预期反而下行——纯真实利率故事。 | ⚠️ conflict |
| C Liquidity 0/3 | 🟢 信用利差仍在自满区、继续收窄，市场尚未对企业信用恶化定价。 HY OAS=266bp, 20dΔ=-8.0 | ✅ |
| E Growth Scare 1/3 | VIX sig: OK | ✅ |
| D Inflation 1/4 | 🟢 外汇与跨境风险扩散暂未启动。 | ✅ |
| CASC Gate | [CASC 确认 0/4 · C端=有序重定价·估值压缩 · 双探针:divergent · 干预守卫=未触发 | ✅ |

> Both systems converge: no structural conflict detected.
