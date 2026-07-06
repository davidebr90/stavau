# Hardware Test Protocol — v0.1 to v1.0

**Purpose:** turn acceptance criteria into executable step-by-step checklists for testing on real hardware. Each procedure records exact commands, expected output, pass/fail decision, and log capture for evidence.

## OS & Test Applicability Matrix

| Test | Windows 10+ | Linux | macOS | Notes |
|---|:---:|:---:|:---:|---|
| SETUP-BASELINE | ✅ | ✅ | ✅ | auto + `--strategy classic_link` variants |
| WALK-AWAY-LOCK | ✅ | ✅ | ✅ | measure 3 runs with stopwatch |
| FALSE-POSITIVE-SOAK | ✅ | ✅ | ✅ | 30 min dry-run, zero LOCK lines |
| CALIBRATION-ACCURACY | ✅ | ✅ | ✅ | 4 distance stations (1/3/5/8 m) |
| MAC-ROTATION | ✅ (limited) | ✅ | ✅ | RPA loss after ~15 min; Windows limited to reachability |
| CLASSIC-LINK-AUDIO | Windows only | ✅ | ✅ | test 6 on Windows is presence-based (AirPods detection limited) |
| GUARDRAIL-TRIP | ✅ | ✅ | ✅ | 3 rapid locks, verify suppression |
| BLUETOOTH-OFF | ✅ | ✅ | ✅ | disable adapter mid-run |

## Prerequisites

- stavau installed and in PATH
- Python 3.9+ environment active
- Real phone (Android, iPhone, or Apple Watch) paired or bondable
- Bluetooth adapter operational
- Test device in clear line-of-sight for distance tests
- Calibrated space: indoor hallway or open room with distance markers (1, 3, 5, 8 m)
- Stopwatch or timing app
- Network monitor (optional, for audit)

---

## Test 1: SETUP-BASELINE

**Acceptance criterion:** Setup wizard completes with auto-detection and forced `--strategy classic_link` variants; `stavau status` reports configuration.

**Procedure:**

1. Clear any existing config:
   ```powershell
   # On Windows
   Remove-Item -Path $env:APPDATA\stavau\config.json -ErrorAction SilentlyContinue
   
   # On Linux/macOS
   rm -f ~/.config/stavau/config.json
   ```

2. Enable Bluetooth and verify adapter is online:
   ```bash
   stavau status --timeout 2
   ```
   - Expected: no config error (will fail to load config, which is expected at this stage)

3. **Run SETUP-AUTO (detect strategy automatically):**
   ```bash
   stavau setup --alias "Test Device" --scan-timeout 10
   ```
   - When prompted to pick device, select your test phone from the list
   - When prompted for distance, enter your measured distance (default 3 m if unknown)
   - When prompted for calibration, stand at 1 m, then 3 m when instructed
   - Expected output snippet:
     ```
     Saved configuration to [path]
     Trusted device: Test Device ([ADDRESS])
     Kind: [iphone|android|unknown]  strategy: [adv_scan|classic_link]  ([paired|pairing-less])
     Safety radius: [3] m, grace time: 5 s
     Guardrail: pause locking after [3] locks within [60] s
     ```

4. Capture evidence:
   ```bash
   stavau log --export > fixtures/setup-baseline-auto.jsonl
   ```
   - [ ] Pass if config.json exists and contains device_address, device_kind, strategy

5. **Run SETUP-CLASSIC-LINK (force classic Bluetooth strategy):**
   ```bash
   rm -f ~/.config/stavau/config.json  # Clear config
   stavau setup --alias "Test Device CL" --scan-timeout 10 --strategy classic_link
   ```
   - Select the same device
   - When prompted, provide distance (default 3 m)
   - Calibrate at 1 m and 3 m as instructed
   - Expected output:
     ```
     Strategy forced to 'classic_link' (overriding auto-detection).
     ```

6. Capture evidence:
   ```bash
   stavau log --export > fixtures/setup-baseline-classic.jsonl
   ```
   - [ ] Pass if strategy in config is `classic_link`

7. **Verify STATUS sanity:**
   ```bash
   stavau status --timeout 8
   ```
   - Expected output includes:
     ```
     device: Test Device ([ADDRESS])
     kind: [...]  strategy: [...]  association: [...]
     rssi: [negative number] dBm ([count] advertisements)
     estimated distance: [number] m - [inside|OUTSIDE] the safety radius
     ```
   - [ ] Pass if RSSI is negative (dBm) and distance is positive

---

## Test 2: WALK-AWAY-LOCK

**Acceptance criterion:** Lock triggers within `grace_seconds + 3 s` of physically exceeding the radius. Test with 3 runs; record wall-clock timings.

**Procedure:**

1. **Setup:** mark a circle on the floor with tape or chalk, radius equal to your configured safety radius (typically 3 m)

2. **Run 1:**
   ```bash
   stavau run
   ```
   - Expected initial state: `state=near` with negative RSSI and distance within radius
   - Start timer when you step **past** the radius marker (cleanly, away from reflective surfaces)
   - Observe console for `state=away` or `>>> screen locked`
   - Stop timer at that exact moment
   - Expected: lock within grace_seconds (typically 5 s) + 3 s = 8 s total
   - [ ] Pass if lock within 8 s; record actual time (seconds): ______

3. **Run 2:**
   - Return to inside the radius; wait for state to return to `near` (observe console)
   - Repeat walk-away; record time: ______
   - [ ] Pass if lock within 8 s

4. **Run 3:**
   - Repeat walk-away; record time: ______
   - [ ] Pass if lock within 8 s

5. Capture evidence:
   ```bash
   stavau log --export > fixtures/walk-away-lock-3runs.jsonl
   ```
   - [ ] Pass overall if all 3 runs within 8 s tolerance

---

## Test 3: FALSE-POSITIVE-SOAK

**Acceptance criterion:** User seated at desk with device for 30 continuous minutes, zero spurious locks in dry-run mode.

**Procedure:**

1. Clear any previous events:
   ```bash
   stavau log --clear
   ```

2. Start dry-run (no actual screen locking):
   ```bash
   stavau run --dry-run --duration 1800
   ```
   - Expected: console prints status every ~2 s with `state=near` and positive distance within radius
   - Device remains on desk or nearby; do not move phone outside the radius
   - Let run for full 30 minutes (1800 s); you may background the process

3. After 30 minutes, stop the run (Ctrl+C or wait for auto-stop)

4. Inspect log for spurious locks:
   ```bash
   stavau log --export | grep LOCK
   ```
   - Expected: zero lines containing `"event": "LOCK"`

5. Full log capture:
   ```bash
   stavau log --export > fixtures/false-positive-soak-30min.jsonl
   ```
   - [ ] Pass if no LOCK events in the entire 30-minute log

---

## Test 4: CALIBRATION-ACCURACY

**Acceptance criterion:** Estimated distance within ±1.5 m at 1, 3, 5, 8 m line-of-sight indoor stations.

**Procedure:**

1. **Re-run calibration to establish baseline:**
   ```bash
   stavau setup --alias "Calibration Test" --skip-calibration
   ```
   - Or reuse existing config and manually note the rssi_at_1m and path_loss_exponent
   - Expected: printed calibration model

2. **Mark four distance stations** (clear line-of-sight, no obstructions):
   - Station A: 1 m from the computer
   - Station B: 3 m from the computer
   - Station C: 5 m from the computer
   - Station D: 8 m from the computer
   - Use a tape measure or meter stick

3. **Test at each station:**
   
   **Station A (1 m):**
   ```bash
   stavau status --timeout 8
   ```
   - Collect the printed distance
   - Expected: distance 1 m ± 1.5 m = **−0.5 to 2.5 m** (pass if within range)
   - Recorded distance: _____ m
   - [ ] Pass if within −0.5 to 2.5 m (accept negative as in-range measurement error)

   **Station B (3 m):**
   ```bash
   stavau status --timeout 8
   ```
   - Expected: distance 3 m ± 1.5 m = **1.5 to 4.5 m**
   - Recorded distance: _____ m
   - [ ] Pass if 1.5 to 4.5 m

   **Station C (5 m):**
   ```bash
   stavau status --timeout 8
   ```
   - Expected: distance 5 m ± 1.5 m = **3.5 to 6.5 m**
   - Recorded distance: _____ m
   - [ ] Pass if 3.5 to 6.5 m

   **Station D (8 m):**
   ```bash
   stavau status --timeout 8
   ```
   - Expected: distance 8 m ± 1.5 m = **6.5 to 9.5 m**
   - Recorded distance: _____ m
   - [ ] Pass if 6.5 to 9.5 m

4. Capture evidence:
   ```bash
   stavau log --export > fixtures/calibration-accuracy-4stations.jsonl
   ```
   - [ ] Pass overall if all 4 stations within tolerance

---

## Test 5: MAC-ROTATION

**Acceptance criterion:** Track a phone via advertisement scanning past its RPA (random private address) rotation (~15 min); document the loss and fail-safe lock response.

**Prerequisite:** Device is configured with `strategy: adv_scan` (typically iPhones on Linux, or any phone without forced `classic_link`).

**Procedure:**

1. Verify strategy is `adv_scan`:
   ```bash
   stavau status --timeout 2
   ```
   - Expected output contains `strategy: adv_scan`
   - If not, this test **skips** — only applicable to adv_scan strategy

2. Start monitoring and note start time:
   ```bash
   stavau run --dry-run
   ```
   - Expected: initial state `near` with positive RSSI and distance within radius
   - Record start time: _____ (HH:MM:SS)

3. **Observe for ~15 minutes** while keeping device within communication range but stationary:
   - Periodically note console output
   - Watch for any `state=away` transitions
   - Record any messages mentioning RPA or address rotation

4. After 15 minutes:
   - Note if still tracking device (state=near) or if lost signal
   - If lost signal:
     - Record time of loss: _____ (HH:MM:SS)
     - Expected: within ~15 min of start
     - Observe if state transitions to `away` (fail-safe lock)
     - [ ] Pass if fail-safe lock triggered when signal lost

5. Stop the run (Ctrl+C)

6. Capture evidence:
   ```bash
   stavau log --export > fixtures/mac-rotation-15min.jsonl
   ```
   - [ ] Pass if either:
     - (a) Device tracked continuously for 15+ min without address rotation (device does not rotate)
     - (b) State transitions to `away` and lock triggered when MAC rotation breaks tracking

---

## Test 6: CLASSIC-LINK-AUDIO

**Acceptance criterion:** Connected audio device (e.g., AirPods, wireless headphones): verify advertisement scanning does NOT detect it, then confirm `--strategy classic_link` tracks it as near; disconnect it, expect fail-safe lock.

**Prerequisite:** Audio device (AirPods, Galaxy Buds, or similar) paired and bondable; test device capable of classic Bluetooth.

**Procedure:**

1. **Part A: Verify adv_scan does not see audio device**
   ```bash
   stavau setup --alias "Audio Test" --skip-calibration --strategy adv_scan
   ```
   - Select the **audio device** from the scan (AirPods, etc.)
   - Expected: config saved, strategy is `adv_scan`

2. Start monitoring in dry-run:
   ```bash
   stavau run --dry-run --duration 30
   ```
   - Expected: console shows state transitions or "no signal"
   - Audio devices typically do not advertise continuously; expect intermittent or no signal
   - Record observation: _____ (device seen / not seen / intermittent)

3. Stop (Ctrl+C or wait 30 s)

4. **Part B: Force classic_link and verify presence detection**
   ```bash
   rm -f ~/.config/stavau/config.json  # Clear config
   stavau setup --alias "Audio Test CL" --skip-calibration --strategy classic_link
   ```
   - Select the same audio device
   - Expected: strategy forced to `classic_link`

5. Start monitoring:
   ```bash
   stavau run --dry-run --duration 60
   ```
   - Expected state: `near` or positive signal (device is bonded and reachable via classic link)
   - Record initial state: _____

6. **Disconnect audio device** (turn off or physically remove) at 30 s mark

7. Observe console for state change:
   - Expected: state transitions to `away` or "no signal" within grace_seconds + 3 s
   - Actual time to away state: _____ s
   - [ ] Pass if transition to away within ~8 s

8. Stop run

9. Capture evidence:
   ```bash
   stavau log --export > fixtures/classic-link-audio.jsonl
   ```
   - [ ] Pass (Part A) if adv_scan shows no/intermittent signal from audio device
   - [ ] Pass (Part B) if classic_link shows near, then away on disconnect

---

## Test 7: GUARDRAIL-TRIP

**Acceptance criterion:** Force 3 rapid locks (walk in/out of radius within short time), expect the 3rd lock followed by suppression messages and no 4th lock during cooldown.

**Procedure:**

1. Clear event log:
   ```bash
   stavau log --clear
   ```

2. Start monitoring in dry-run:
   ```bash
   stavau run --dry-run
   ```

3. **Lock #1:**
   - Start inside radius with state=near
   - Walk outside radius, wait for lock message (state=away)
   - Record time: _____ s
   - [ ] Confirm: `>>> LOCK (dry-run: screen not actually locked)` or similar

4. **Walk back inside radius**, wait for state to return to `near` (may take a few seconds)

5. **Lock #2:**
   - Walk outside radius again, wait for lock
   - Record time: _____ s
   - [ ] Confirm: lock message appears

6. **Walk back inside radius**, wait for state to return to `near`

7. **Lock #3:**
   - Walk outside radius again, wait for lock
   - Record time: _____ s
   - [ ] Confirm: lock message appears

8. **Attempt to trigger Lock #4 (should be suppressed):**
   - Walk back inside radius, wait for state=near
   - Walk outside radius again
   - Expected: **guardrail suppression message** instead of lock:
     ```
     >>> guardrail active: lock SUPPRESSED, resuming in [X] s
     ```
   - [ ] Pass if 4th lock is suppressed (not executed)

9. **Wait for cooldown** (typically 60 s per config default `breaker_window_seconds`)
   - After cooldown expires, attempt one more walk-away
   - Expected: lock message resumes (guardrail reset)
   - [ ] Pass if lock resumes after cooldown

10. Stop run (Ctrl+C)

11. Capture evidence:
    ```bash
    stavau log --export > fixtures/guardrail-trip-3locks.jsonl
    ```
    - [ ] Pass overall if:
      - Locks 1–3 triggered
      - Lock 4 suppressed with guardrail message
      - Lock resumes after cooldown

---

## Test 8: BLUETOOTH-OFF

**Acceptance criterion:** Armed run, disable Bluetooth adapter mid-run, expect lock within grace period with an explanatory log entry.

**Procedure:**

1. Start monitoring in armed mode (not dry-run):
   ```bash
   stavau run
   ```
   - Expected initial state: `near` with device connected
   - Record start time: _____ (HH:MM:SS)

2. **After 10 seconds, disable Bluetooth adapter:**
   
   **On Windows:**
   ```powershell
   # Disable Bluetooth via Settings or via command (if available)
   # Or use: Settings → Bluetooth & devices → Bluetooth toggle OFF
   ```
   
   **On Linux:**
   ```bash
   bluetoothctl power off
   ```
   
   **On macOS:**
   ```bash
   # System Preferences → Bluetooth → Turn Off
   # Or: blueutil off
   ```

3. **Observe console for lock message:**
   - Expected: `>>> screen locked` (or dry-run equivalent) within grace_seconds + 3 s
   - Actual time from disable to lock: _____ s
   - [ ] Pass if lock within ~8 s

4. Inspect log for Bluetooth-off entry:
   ```bash
   stavau log --count 5
   ```
   - Expected: a recent entry describing Bluetooth off or adapter unavailable
   - [ ] Pass if explanatory log entry present

5. Re-enable Bluetooth for subsequent tests

6. Capture evidence:
   ```bash
   stavau log --export > fixtures/bluetooth-off-fail-safe.jsonl
   ```
   - [ ] Pass if lock triggered and log contains context

---

## Test Results Summary

Complete this table after running all applicable tests:

| Test ID | OS | Device | Device Kind | Strategy | Pass/Fail | Evidence File | Notes |
|---|---|---|---|---|:---:|---|---|
| SETUP-BASELINE | | | | auto | [ ] | fixtures/setup-baseline-auto.jsonl | |
| SETUP-BASELINE | | | | classic_link | [ ] | fixtures/setup-baseline-classic.jsonl | |
| WALK-AWAY-LOCK | | | | | [ ] | fixtures/walk-away-lock-3runs.jsonl | Run 1: ___ s, Run 2: ___ s, Run 3: ___ s |
| FALSE-POSITIVE-SOAK | | | | | [ ] | fixtures/false-positive-soak-30min.jsonl | 30 min, zero locks |
| CALIBRATION-ACCURACY | | | | | [ ] | fixtures/calibration-accuracy-4stations.jsonl | 1m: ___ m, 3m: ___ m, 5m: ___ m, 8m: ___ m |
| MAC-ROTATION | | | | adv_scan | [ ] | fixtures/mac-rotation-15min.jsonl | ~15 min track or rotation loss |
| CLASSIC-LINK-AUDIO | | | audio | adv_scan | [ ] | fixtures/classic-link-audio.jsonl | Part A: no/intermittent |
| CLASSIC-LINK-AUDIO | | | audio | classic_link | [ ] | fixtures/classic-link-audio.jsonl | Part B: near then away |
| GUARDRAIL-TRIP | | | | | [ ] | fixtures/guardrail-trip-3locks.jsonl | Locks 1–3, 4 suppressed |
| BLUETOOTH-OFF | | | | | [ ] | fixtures/bluetooth-off-fail-safe.jsonl | Time to lock: ___ s |

**Overall result:** [ ] All tests passed  [ ] Some tests failed (list below)

**Failed tests and root cause:**
- 
- 
- 

**Tester name:** ________________  
**Date:** ________________  
**Platform & Bluetooth adapter model:** ________________
