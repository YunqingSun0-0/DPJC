# FHE Test Scripts — Goals and Usage

A practical reference for the Python and bash scripts that drive the FHE PSI tests in this repo.

## Prerequisites

Build the project first so the binaries exist. CMake is configured to build in place; from the repo root:

```bash
cmake .          # only needed once (or after editing CMakeLists.txt)
make -j$(nproc)
```

This produces `./bin/gendata`, `./bin/psi_server`, `./bin/psi_client`. All scripts in this guide call `./bin/...`.

Python deps for the batch harness and plotting: `numpy`, `matplotlib`, `pandas`.

---

## Python drivers

### `fhe_test_single.py`

**Goal.** Run one FHE PSI execution end-to-end (data gen → 2 servers + 2N clients → parse timings → validate intersection size). This is the workhorse all the bash scripts wrap.

**What it does:**
1. Kills lingering `psi_server` / `psi_client` processes.
2. Runs `./bin/gendata` to materialize each client's input set with a known intersection.
3. Spawns 2 servers and `2 × num_clients_per_server` clients with `--psi_mode=fhe`.
4. Streams stdout, regex-parses timing/communication metrics (`Key generation time`, `Client computation time`, `Server recovery time`, `Final PSI size`, etc.).
5. Asserts the recovered PSI size matches the expected intersection within 10% tolerance.

**Usage:**
```bash
python3 fhe_test_single.py 
# define your own parameter
python3 fhe_test_single.py --set_size_bit 1 --prg_dd 7  --seed_size_bit 8 --num_clients_per_server 1 --output_log fhe_test_small_unweighted.log -v  
# add small weight
## We currently requires final output < 2^14,  (corresponding to --max_weight 128, --set_size_bit 1).
python3 fhe_test_single.py \
  --set_size_bit 1 --prg_dd 7 --seed_size_bit 8 \
  --num_clients_per_server 1 --max_weight 128 \
  --output_log fhe_test_small_weighted.log -v
```
Exits 0 on pass, 1 on fail.

### `Large-SEAL parameter` test


Switch to `large-seal profile` (`16384 / 24 / {54, 54, 54, 54, 50, 18}`), rebuild, and run weighted overflow test:

```bash
perl -0777 -i -pe 's/size_t seal_degree = \d+;/size_t seal_degree = 16384;/; s/size_t seal_plain_modulus = \d+;/size_t seal_plain_modulus = 24;/; s/std::vector<int> seal_coeff_modulus = \{[^}]+\};/std::vector<int> seal_coeff_modulus = {54, 54, 54, 54, 50, 18};/;' include/config.h && \
cmake . && make -j"$(nproc)" && \
## We currently requires final output < 2/3p, (for p=2^24, corresponds to --set_size_bit 1  and --max_weight 3200).  For larger output, requires CRT + multi-limb
python3 fhe_test_single.py \
  --set_size_bit 1 \
  --prg_dd 8 \
  --seed_size_bit 8 \
  --num_clients_per_server 1 \
  --max_weight 3200 \
  --output_log fhe_test_large_weighted.log \
  -v
```


Switch back to `default (current)` profile (`8192 / 24 / {60, 60, 36, 27, 27}`) and rebuild:

```bash
perl -0777 -i -pe 's/size_t seal_degree = \d+;/size_t seal_degree = 8192;/; s/size_t seal_plain_modulus = \d+;/size_t seal_plain_modulus = 24;/; s/std::vector<int> seal_coeff_modulus = \{[^}]+\};/std::vector<int> seal_coeff_modulus = {60, 60, 36, 27, 27};/;' include/config.h && \
cmake . && make -j"$(nproc)"
```

## Bash tests
### `fhe_run_seed_tests.sh`

**Goal.** Sweep seed sizes and capture key-gen time and communication overhead. Supports LAN (single host) and a real two-machine WAN deployment with `tc`-based throttling via [`throttle.py`](throttle.py).

**Local mode (default)** — wraps `fhe_test_single.py`:
```bash
./fhe_run_seed_tests.sh                       # default seed bits = (10)
./fhe_run_seed_tests.sh --seed-bits "7 8 9 10"   # custom sweep
./fhe_run_seed_tests.sh --mode local --set-size-bit 5 --prg-dd 6
```

**WAN mode** — runs `psi_server`/`psi_client` directly with `--server1_host`/`--server2_host`. Run once on each machine, with `server1` started first:
```bash
# On server 1 host
./fhe_run_seed_tests.sh --mode wan --role server1 \
    --server1-host 127.0.0.1 --server2-host 127.0.0.1 \
    --generate-data --seed-bits "7"

# On server 2 host (same flags except --role)
./fhe_run_seed_tests.sh --mode wan --role server2 \
    --server1-host 127.0.0.1 --server2-host 127.0.0.1 \
    --generate-data --seed-bits "7"
```
**Default WAN throttle** (applied via [`throttle.py`](throttle.py) before the seed loop):

| Setting | Default | Override flag |
|---|---|---|
| Interface | `auto` (first non-`lo` from `ifconfig`) | `--throttle-interface` |
| Bandwidth | `200` Mbit/s | `--wan-bandwidth-mbit` |
| One-way latency | `40` ms | `--wan-latency-ms` |

Remove manually after a crashed run:
```bash
python3 throttle.py -i auto -d
```

### `fhe_run_client_compute_tests.sh`

**Goal.** Sweep `prg_dd ∈ {4,5,6,7,8}` against client sizes (`CLIENT_SIZES_BITS` array, currently just `0` → 1 element) and extract per-element client computation time. Each cell can be repeated N times and averaged — pass the run count as the first positional arg (default 1).

```bash
./fhe_run_client_compute_tests.sh         # 1 run per cell (single data point)
./fhe_run_client_compute_tests.sh 20      # 20 runs per cell, averaged
```
Edit `CLIENT_SIZES_BITS` near the top to expand the size axis (the comment lists 0/4/8/12).

### `fhe_run_agg_time.sh`

**Goal.** Isolate the **client-results aggregation phase** and study how it scales with per-server client count. Greps `Client results aggregation time:` from server output and reports per-server values.

Usage: `./fhe_run_agg_time.sh [CLIENTS_PER_SIDE]`

| Knob | Where | Default | Meaning |
|---|---|---|---|
| `CLIENTS_PER_SIDE` | positional `$1` | `100` | Per-server client count |

```bash
./fhe_run_agg_time.sh 10        # 10 clients per side
./fhe_run_agg_time.sh 1         # smallest setup (2 clients total)
```

Logs land in `fhe_phase/agg_time_<CLIENTS_PER_SIDE>cps_{full,summary}_<timestamp>.log`.


## Common gotchas

- **Binary location.** All scripts call `./bin/...` from the repo root. CMake builds in place — no separate `build/` directory.
- **Lingering processes.** Failed runs can leave `psi_server`/`psi_client` holding ports. Use `pkill -f psi_server; pkill -f psi_client`.
- **WAN throttle.** `fhe_run_seed_tests.sh --mode wan` applies `tc` rules via `throttle.py`. If the script crashes mid-run, remove them manually with `python3 throttle.py -i <iface> -d`.
- **Timeouts.** `fhe_test_single.py` defaults to `TIMEOUT_SECONDS = 7200` (2h). Bump it for very large set sizes.
- **Output regex coupling.** All extraction relies on exact log lines like `Key generation time: 1.234s` and `Client results aggregation time: ... ms (... us)`. Any change to the C++ logging format ([src/psi.cpp](src/psi.cpp)) will silently break the parsers.
