# Fed Reaction Dashboard v2

**2026-06-24 09:32:06 CST** | **2026-06-24 01:32:06 UTC** | Futu + yfinance

## 0. State Machine: **⏸ OBSERVE** (Day 8)

> No trigger B=1 C=0 E=2

> **To upgrade**: B>=3 (now 1/4) | E<=1 (now 2/3)

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
| SHY | $81.97 | +0.07% | -0.19% | 2Y proxy |
| IEF | $94.12 | +0.13% | -0.42% | 10Y proxy |
| TLT | $86.2 | +0.13% | +0.01% | long-end |
| UUP | $28.45 | +0.32% | +1.86% | USD |
| GLD | $377.32 | -1.89% | -5.11% | Gold |
| QQQ | $713.65 | -3.29% | -2.11% | Nasdaq |
| SPY | $733.58 | -1.45% | -1.98% | S&P500 |
| IWM | $295.32 | -0.96% | +1.11% | Russell |
| HYG | $79.87 | -0.09% | -0.20% | HY credit |
| LQD | $108.91 | +0.12% | -0.19% | IG credit |
| VXX | $23.87 | +5.99% | +5.76% | Volatility |
| CL | $91.43 | +3.11% | +0.85% | WTI |

## 3. VIX

**16.91** (daily +0.51)

## 4. Score Modules

| Module | Score | Max | Strength | Details |
|--------|-------|-----|----------|--------|
| A. Hawkish | 3 | 4 1strong | DXY_up +0.32% z=1.1; Gold_down -1.89% z=1.1; Nasdaq_weak -3.29% z=1.7 (强); — 以下未达阈值 —; SHY_down=2Y_up +0.07% (thresh -0.05%, z=0.6) |
| B. Dovish | 1 | 4 | SHY_up=2Y_down +0.07% z=0.6; — 以下未达阈值 —; DXY_down +0.32% (thresh -0.05%, z=1.1); Gold_up -1.89% (thresh +0.30%, z=1.1); Nasdaq_strong -3.29% (thresh +0.50%, z=1.7) |
| C. Liquidity | 0 | 3 | VIX=16.91 (thresh >18); HYG z=+1.55 LQD z=+0.87 spread=+0.68 (credit neutral); IWM-SPY=+0.49% (thresh <-0.30%) |
| D. Inflation | 1 | 4 | curve: 5Y_5d=-6.8 10Y_5d=-6.5 30Y_5d=-4.9 (no bear-steepen/bear-flatten); TLT+0.13% (thresh <-0.50%); WTI++3.11% (need BEI confirm) |
| E. Growth | 2 | 3 | SHY&IEF both up: SHY+0.07% IEF+0.13% (2Y&10Y down); IWM-SPY=+0.49% (thresh <-0.30%); QQQ-IWM divergence -2.33% |

## 6. Curve Signals

- **WARN**: Growth-scare type cut

## 7. 2Y/10Y Interpretation

> Note: 2Y proxy=SHY, 10Y proxy=IEF; ETF up = yield down
- **2Y proxy(SHY)**: yield_down(dovish) (+0.07%)
- **10Y proxy(IEF)**: yield_down (+0.13%)
- **30Y(TLT)**: yield_down (+0.13%)
- **10Y=4.487% < 4.6%**: manageable

## 8. ABCD Cross-Validation

| This Tool | ABCD Reading | Match? |
|-----------|-------------|--------|
| A Hawkish 3/4 | 🔴 长端贴现率/真实利率压力已很高，通胀预期反而下行——纯真实利率故事。 | ⚠️ conflict |
| C Liquidity 0/3 | 🟢 信用利差仍在自满区、继续收窄，市场尚未对企业信用恶化定价。 HY OAS=266bp, 20dΔ=-8.0 | ✅ |
| E Growth Scare 2/3 | VIX sig: OK | ⚠️ |
| D Inflation 1/4 | 🟢 外汇与跨境风险扩散暂未启动。 | ✅ |
| CASC Gate | [CASC 确认 0/4 · C端=有序重定价·估值压缩 · 双探针:divergent · 干预守卫=未触发 | ✅ |

> Both systems converge: no structural conflict detected.
