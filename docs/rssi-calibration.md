# RSSI → Distance Calibration

## Why calibration is mandatory

RSSI at a given distance varies by **±10 dBm or more** across adapter models, device orientation, antenna placement and environment. A hardcoded RSSI→distance table would be wrong almost everywhere. stavau therefore fits the model **per user, per environment** during setup.

## Model

Log-distance path loss:

```
RSSI(d) = RSSI₁ₘ − 10 · n · log₁₀(d)
   ⇒ d = 10 ^ ((RSSI₁ₘ − RSSI) / (10 · n))
```

- `RSSI₁ₘ` — reference power at 1 m (typically −45 to −65 dBm)
- `n` — path loss exponent (free space ≈ 2.0; indoor with obstacles 1.8–3.5)

## Wizard procedure (v0.3; CLI equivalent in v0.1)

1. Bond the trust device and confirm a stable link.
2. **Station 1:** user stands at 1 m with the phone in its usual position (pocket!). Collect ≥ 30 samples, keep the median → `RSSI₁ₘ`.
3. **Station 2 (3 m)** and **Station 3 (5 m)**: same collection; least-squares fit of `n` over all stations.
4. Sanity check: if fitted `n` < 1.2 or > 4.5, warn and suggest re-running (likely movement or reflection artefacts).
5. Store `RSSI₁ₘ`, `n`, fit residual in config; show the user their estimated-vs-real table.

**Practical tip surfaced by the wizard:** calibrate with the phone where you actually carry it. A phone in a back pocket reads ~5–10 dBm weaker than one held in hand (body attenuation).

## Runtime filtering

1. **Median pre-filter** (window 3) kills single-sample spikes.
2. **Moving average** (window 8, ≈ 8 s at 1 Hz) smooths fading.
3. **Hysteresis** in the presence state machine (leave at `radius`, return at `0.8 · radius`) + dwell timers.

## Accuracy target (Definition of Done)

±1.5 m in the 1–10 m range, indoor line-of-sight, after calibration — verified by the acceptance tests in [acceptance-criteria.md](acceptance-criteria.md). Non-line-of-sight error is expected to be worse and is compensated by the grace time, not by the model.
