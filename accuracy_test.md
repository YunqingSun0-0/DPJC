# Accuracy Testing Guide

## Real World Data Testing

`accuracy_test_realworld.py` needs a UCI Bag-of-Words file. The repo does not include data — download it into `./uci_words/`.

### Download NYTimes (default)

```bash
mkdir -p uci_words
curl -L -o uci_words/docword.nytimes.txt.gz \
  https://archive.ics.uci.edu/ml/machine-learning-databases/bag-of-words/docword.nytimes.txt.gz
gunzip uci_words/docword.nytimes.txt.gz
```

~250 MB download, ~1 GB unpacked. First multi-doc run also writes a `*.cache.npz` next to it (~500 MB) for fast reload.

### Run

Prereqs:
- `./bin/{psi_server, psi_client, gendata}` built (see top-level readme)
- A `docword.<corpus>.txt` in `./uci_words/` (above)
- Python: `numpy`, `pandas`, `matplotlib`

Default experiment: each side draws enough NYTimes documents to total ~2^18 word-count entries (NNZ). For every `k` value the script runs our sketch (sweeping `seed_size_bit`) and the standard AMS sketch with uniform randomness as a baseline, then plots the accuracy comparison.

Parameters:

| name | value |
|---|---|
| `--set-size` | `262144` (2^18, target NNZ per side) |
| `MOM_K_VALUES` | `[100, 200, 400, 1000, 2500, 10000]` |
| `MOM_T` | `11` |
| `PRG_DD` | `7` |
| `SEED_VALUES` (ours) | `[6, 7, 8]` |

```bash
python3 accuracy_test_realworld.py --parallel --set-size 262144
```

### Output

Each run creates `./experiments/realworld_<timestamp>/`:

- `results/batch_test_results.json` — raw per-run results (resumable: rerun with the same `--run-dir` and finished `(k, seed, mode)` combinations are skipped)
- `results/analysis_results.json`, `realworld_errorvsepsilon_summary.csv` — stats per `(k, seed, mode)` after trimming the top/bottom 5% of runs
- `plots/realworld_error_vs_epsilon.{png,pdf}` — the main figure

### Notes

- The script runs `pkill -f psi_server / psi_client` on startup; close any other PSI jobs first.
- `mom_k * mom_t` must stay ≤ 8192 (global protocol limit).
