# Multiple Route Comparison

This will compare the routes provided the `*_summary.json` using normalized: time, net DC Energy, Relative SiC Damage. The lower score is better.

The default weights are:

- time = 0.40
- energy = 0.30
- damage = 0.30

Created two files: `compare_routes.py` and `run_co

Example Usage:

```bash
python -u .\sim\src\drive_cycles\run_compare_routes.py `
  .\sim\cycles\route_A_summary.json `
  .\sim\cycles\route_B_summary.json `
  .\sim\cycles\route_C_summary.json `
  --time-weight 0.50 `
  --energy-weight 0.20 `
  --damage-weight 0.30
```

- You can have as many `*_summary.json` files, and you can change the weights of what you want the route to prioritize, and which route would be the best in that aspect.

