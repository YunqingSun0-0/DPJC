# PSI

A two-server multi-client Private Set Intersection prototype with two backends: a `naive` mode for accuracy experiments and an `fhe` mode for end-to-end secure performance benchmarks.

This README only covers what's needed to build and identify the binaries / scripts. For actual direct test guide (parameter sweeps, plotting, real-world data, WAN runs) see:

- [accuracy_test.md](accuracy_test.md) — accuracy experiments (`naive` mode, synthetic + UCI BoW data)
- [fhe_test.md](fhe_test.md) — FHE performance experiments (single-run driver + bash sweeps)

## Dependencies

System libraries:

- A C++17 toolchain and CMake ≥ 3.10
- OpenSSL
- [emp-tool](https://github.com/emp-toolkit/emp-tool), [emp-ot](https://github.com/emp-toolkit/emp-ot), emp-sh2pc
- [Microsoft SEAL](https://github.com/microsoft/SEAL) ≥ 4.1 (FHE backend)

Python (only needed for the test drivers):

- `numpy`, `pandas`, `matplotlib`

## Build

CMake builds in place at the repo root — there is no separate `build/` directory:

```bash
cmake .          # only needed once (or after editing CMakeLists.txt)
make -j$(nproc)
```

This produces three binaries under [bin/](bin/):

| Binary | Purpose |
|---|---|
| [bin/gendata](bin/) | Generates synthetic client input sets with a controlled intersection size. Used to seed every `naive` accuracy run and every `fhe` performance run. |
| [bin/psi_server](bin/) | One of the two PSI servers (`-p 1` or `-p 2`). Selects the backend with `--psi_mode={naive,fhe}`. |
| [bin/psi_client](bin/) | A PSI client process. Each server has `num_clients_per_server` clients attached; clients on server 1 use ids in `[1, N]`, on server 2 in `[N+1, 2N]`. |

Default parameter values (universe size, seed size, MoM `k`/`t`, SEAL parameters, etc.) live in [include/config.h](include/config.h). `mom_k * mom_t` must stay ≤ SEAL degree.

## Where each binary is used

- **Accuracy experiments** ([accuracy_test.md](accuracy_test.md)) drive `gendata` + `psi_server`/`psi_client` in `naive` mode through three Python scripts:
  - `accuracy_test_single.py` — single configuration smoke test
  - `accuracy_test_batch.py` — synthetic-data sweeps (`seed_optimization`, `errorvsepsilon`)
  - `accuracy_test_realworld.py` — UCI Bag-of-Words sweeps with `naive` vs `naive_uniform`
- **FHE performance experiments** ([fhe_test.md](fhe_test.md)) drive the same binaries in `fhe` mode:
  - `fhe_test_single.py` — single end-to-end FHE run (gen data → 2 servers + 2N clients → parse timings → check intersection)
  - `fhe_run_seed_tests.sh`, `fhe_run_client_compute_tests.sh`, `fhe_run_agg_time.sh` — bash wrappers for specific FHE sweeps; LAN by default, WAN supported via [throttle.py](throttle.py)

## Quick sanity check

Pick the matching guide and run its single-config script first; both are structured like a unit test (data gen → run → assertion):

```bash
# accuracy
python3 accuracy_test_single.py

# fhe
python3 fhe_test_single.py
```

Each exits 0 on success. From there, follow the corresponding `*.md` for sweeps and plotting.
