# Validation and Calibration (5)

```
route
→ speed/grade mission profile
→ vehicle longitudinal power (power mission profile)
→ inverter electrical loss
→ junction temperature
→ rainflow cycles
→ damage index
```

To calibrate this we need to make sure every stage is tied to real data independently. *Validating only the final damage number won't fix anything*

Validation Plan:

1. **Vehicle/route layer:** compare energy consumption and speed behavior against real vehicle benchmarks.
2. **Inverter layer:** replace placeholder SiC parameters with one real MOSFET/module datasheet.
3. **Thermal layer:** validate your RC/Foster model against datasheet transient thermal impedance.
4. **Reliability layer:** replace the placeholder Coffin–Manson coefficients with published power-cycling data for a comparable package.

- Only after those are validated should you interpret absolute lifetime.

---

Based off Wolfspeed's Power Cycling and Lifetime Modeling Approach, our model will follow the same process.

- https://assets.wolfspeed.com/uploads/2025/11/Wolfspeed_Power_Cycling_and_Lifetime_Modeling_Approach.pdf
- https://assets.wolfspeed.com/uploads/2025/02/Wolfspeed_CAB525F12XM3_data_sheet.pdf
- electro-thermal junction-temperature history → rainflow counting → stress evaluation using **ΔTj, Tj,max, and cycle duration** → cumulative Miner damage. 



From the previous implementation the big improvement is that rainflow cycles now retain timing information, so each cycle contains this info:

```
ΔTj
mean Tj
Tj,max
cycle count
start time
end time
thermal excursion duration
```

The reliability calculation is now conceptually:

```
Relative severity
    =
ΔTj factor
× maximum-temperature factor
× duration factor

Route damage
    =
Σ(cycle count × relative severity)
```



**Usage:**

The new F7 -> tab 5. Reliability tab has two tests:

1. `Validate damage model`

   1. This checks that
      ```
      2× cycle count → 2× accumulated damage
      higher ΔTj     → higher damage
      higher Tj,max  → higher damage
      longer cycle   → higher damage
      ```

      

2. `Run WLTC Reliability Mission`

   1. Output is still:
      ```
      Relative damage index
      Damage vs best
      Equivalent rainflow cycles
      Most damaging thermal cycle
      Damage-weighted ΔTj
      Damage-weighted Tj,max
      Damage-weighted cycle duration
      ```



**Test 1**:

```WLTC CLASS 3 RELIABILITY MISSION ANALYSIS
====================================================================

METHOD
  Thermal history:        CAB525F12XM3 electro-thermal model
  Cycle extraction:       rainflow counting
  Damage accumulation:    Palmgren-Miner linear accumulation
  Stress variables:       Delta Tj, Tj,max, excursion duration

MISSION DAMAGE
  Relative damage index:  1.600415e-05
  Equivalent cycles:      207.00
  Max cycle contribution: 4.925223e-06
  Most damaging cycle:    212

DAMAGE-WEIGHTED STRESS
  Delta Tj:               9.54 C
  Tj,max:                 69.66 C
  Excursion duration:     161.68 s

WOLFSPEED POWER-CYCLING CONTEXT
  Inside published PC temperature envelope: 0.00 cycles (0.0 %)
  Outside published PC temperature envelope: 207.00
  PCsec-like durations:   132.00
  Transition durations:   45.00
  PCmin-like durations:   30.00

QUALIFICATION
  Route-to-route relative ranking: ENABLED
  CAB525 absolute cycles-to-failure: NOT CALIBRATED
  Years of life / remaining useful life: NOT CLAIMED

  The manufacturer publishes the power-cycling methodology and typical stress
  ranges, but not CAB525F12XM3-specific life-model coefficients. Relative
  damage is therefore the scientifically defensible output for this model.
```

- Key Values:

  ```
  Relative damage index:  1.6004e-05
  Equivalent cycles:      207
  Damage-weighted ΔTj:    9.54°C
  Damage-weighted Tj,max: 69.66°C
  ```

  - Looks good.





