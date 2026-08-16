# Visualization Update

This update to the visualization GUI, separates generating experiments from analyzing experiments. When launching `visualization.py`, you can press `F6` to launch the Mission Profile Lab Window.

- Three files updated/modified: `visualization.py`, `mission_profile_window.py`, `mission_profile_analysis.py`

The new window `mission_profile_window.py` automatically scans `sim/cycles/` for the raw mission profiles (not the derives files), and you can press `ctrl` or `shift` to select several profiles. Then click: analyze selected.

- A comparison table then shows:

  Mission profile
  Time
  Distance
  Net DC energy
  Peak junction temperature
  Maximum Delta Tj
  Relative SiC damage
  Equivalent thermal cycles

This makes it easy to compare different profiles.

You can then choose to export the research bundle to `research_exports/`

