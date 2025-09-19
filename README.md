Goon Squad Galaxy Simulator (minimal)

This repository contains a minimal copy of the game simulator used for development and testing.

Included
- `gsg_sim.py` — simulator core (minimal import copy)
- `narc_deck_strict.json`, `pcu_deck_strict.json` — starter deck data
- `tests/` — unit tests for burn/deck/ability behavior
- `.github/workflows/ci.yml` — GitHub Actions to run pytest

Quick start

1. Create a virtual environment (recommended) and install test deps:

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

2. Run the tests:

   pytest -q

Notes
- This repo deliberately omits large game logs and historical artifacts from the original workspace. If you want the full history, we can discuss using Git LFS or rewriting history to remove large files.
