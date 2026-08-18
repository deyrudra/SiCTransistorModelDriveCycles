# Validation and Calibration (3)



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

In this document I will be working on the Inverter Layer.

I will stop using the placeholder MOSFET values and now use values that belong to an actual traction-inverter.

- I will use the `Wolfspeed CAB525F12XM3` as the SiC MOSFET Half-Bridge Module.
  - 1200 V, 2.6mΩ
  - Used for traction drives/e-mobility
  - 525 A RMS 
  - Datasheet: https://assets.wolfspeed.com/uploads/2025/02/Wolfspeed_CAB525F12XM3_data_sheet.pdf
    - I stored this pdf file under `docs/mosfet_specs`

- What is a Half-Bridge Module?
  - A SiC half-bridge module is a packaged power circuit which contains two SiC MOSFETs arranged as a half bridge. 
  - ![image-20260818140510858](./assets/image-20260818140510858.png)
  - By switching the upper and lower MOSFETs alternately, you can make the output switch between DC+ and DC-.
  - This is the basic building block for things like:
    - EV Inverters
    - Solar Inverters
    - Motor Drives
    - DC/DC Converters
    - Industrial Power Supplies
    - On-board Chargers
  - **A three-phase inverter** uses three half-bridges, giving you six MOSFETs total (*all are NMOS*)

**Updating YAML with MOSFET Config**: `tesla_model3_lr_rwd.yaml`

