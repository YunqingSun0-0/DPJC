# 便捷 python 程序

`accuracy_test_single.py` 测试单组数据，跑通后跑 `accuracy_test_batch.py` 测试多组数据然后自动画图

注意: 参数调整在每个文件的最开始，注意修改文件夹，免得数据背覆盖了; 可以单独运行跑数据或着画图(main里面注释一部分函数)

fhe 的几个程序，我本机跑不了。我估计是psi-mode换了应该能跑成功，但是集成的python不知道有没有问题

`fhe_test_single.py` 测试单组数据，跑通后跑 `fhe_performance_test_batch.py` 测试多组数据然后会输出各个部分的时间和commu size, `fhe_performance_test_batch.py` 的参数在 `fhe_test_config.py` 里

fhe 的正确性可以通过不开 --test_mode (但是默认开了我已经，只能在 config.h 里改了) 来测试，不开的话造数据的 seed 固定，这样只用检查 naive 和 fhe 的结果是否一样就行了

lan wan 没有实现

数据全部都存到指定文件夹下了

# 可执行文件运行示例

## 生成数据

随机数据然后均分

```bash
./build/bin/gendata --intersection_size=500 --universal_size_bit=18 --num_clients_per_server=1 --set_size=1000 --output_dir=./test_naive_data
```

## Server

```bash
# server 1
./build/bin/psi_server -p 1 --port=20929 --psi_mode=naive --num_clients_per_server=1 --test_mode --mom_k=100 --mom_t=80
# server 2
./build/bin/psi_server -p 2 --port=20929 --psi_mode=naive --num_clients_per_server=1 --test_mode --mom_k=100 --mom_t=80
```

-p 表示服务器 id, server 是 1 和 2, client 从 1 开始编号，[1, num_clients_per_server] 是 server 1 的 client id 范围，[num_clients_per_server + 1, 2 * num_clients_per_server] 是 server 2 的 client id 范围

参数默认值在 `./include/config.h` 中

mom_k * mom_t 目前不能超过 8192

## Client

```bash
# client for server 1 (id=1)
./build/bin/psi_client -p 1 --port=20929 --data_file=./test_naive_data/client1_1.txt --psi_mode=naive --num_clients_per_server=1 --test_mode --mom_k=100 --mom_t=80 --prg_dd=6
# client for server 2 (id=2)
./build/bin/psi_client -p 2 --port=20929 --data_file=./test_naive_data/client1_2.txt --psi_mode=naive --num_clients_per_server=1 --test_mode --mom_k=100 --mom_t=80 --prg_dd=6
```

server 1 对应 client 的数据编号是 clienti_1.txt, server 2 对应 client 的数据编号是 clienti_2.txt, $i\in[1,num_clients_per_server]$

# `accuracy_test_realworld.py`

基于 UCI Bag-of-Words 真实数据测 PSI accuracy: error vs $1/\sqrt{k}$，并 sweep `seed_size_bit`。同一组数据会跑 `naive` 和 `naive_uniform` 两种 mode 做对比。

## 准备

- 编译好 `./bin/psi_server`、`./bin/psi_client`，1-doc 模式还需要 `./bin/gendata`
- 准备 UCI BoW 数据放到 `./uci_words/`，默认 `docword.nytimes.txt`，可在脚本顶部 `UCI_DATA_FILE` 改
- Python 依赖: `numpy`, `pandas`, `matplotlib`

## 两种数据模式

| 模式 | 触发 | 行为 |
|---|---|---|
| 1-doc (默认) | `SET_SIZE = None` | 每个 client 一篇文档，调用 `./bin/gendata` 生成 |
| multi-doc | `SET_SIZE = N` | Python 选若干篇文档使每边总 NNZ ≈ N，均分到 `NUM_CLIENTS_PER_SERVER` 个 client |

multi-doc 模式第一次会在 `UCI_DATA_FILE` 旁写 `*.cache.npz`，之后加载只需几秒。

注: PSI 看到的是 unique word 集合（≤ vocab size W），`SET_SIZE` 控制的是输入量，不是协议看到的元素数。

## 关键参数 (脚本顶部，CLI 可覆盖)

| 参数 | 默认 | 说明 |
|---|---|---|
| `UCI_DATA_FILE` | `./uci_words/docword.nytimes.txt` | 任意 UCI BoW 文件 |
| `NUM_CLIENTS_PER_SERVER` | 1 | 每边 client 进程数 |
| `UNIVERSAL_SIZE_BIT` | 24 | 必须和 `psi_server` / `psi_client` 编译时一致 |
| `PORT_BASE` | 22000 | 端口基址 |
| `SET_SIZE` | `None` | 见上表 |
| `MOM_K_VALUES` | `[100, 200, 400, 1000, 2500, 10000]` | 横坐标 k 取值 |
| `PRG_DD` / `MOM_T` | 7 / 11 | PRG 和 MoM 参数 |
| `SEED_VALUES` | `[6, 7, 8]` | `naive` 的 `seed_size_bit` sweep |
| `UNIFORM_SEED` | 7 | `naive_uniform` 的固定 seed |
| `NUM_RUNS_PER_POINT` | 1000 | 每个 (k, seed, mode) 跑的次数 |
| `TIMEOUT_SECONDS` | 200 | 单次运行超时 |

## 命令

```bash
# 顺序跑 (1 doc/client)
python accuracy_test_realworld.py --run-tests

# 顺序跑 (multi-doc，每边 ≈ 2^18 NNZ)
python accuracy_test_realworld.py --run-tests --set-size 262144

# 并行跑，结束自动 analyze + plot
python accuracy_test_realworld.py --parallel
python accuracy_test_realworld.py --parallel --parallel-workers 8 --set-size 262144

# 用已有结果分析 + 出图
python accuracy_test_realworld.py --analyze --plot --run-dir ./experiments/realworld_YYYYMMDD_HHMMSS

# 只从 summary CSV 重画图 (不重新分析)
python accuracy_test_realworld.py --plot-only --run-dir ./experiments/realworld_YYYYMMDD_HHMMSS
```

## CLI 参数

| flag | 说明 |
|---|---|
| `--run-tests` | 顺序跑所有 (k, seed, mode) 组合 |
| `--parallel` | 启动 worker 并行跑，结束后自动 analyze + plot |
| `--parallel-workers N` | worker 数量；不传则按 `cpu / (2 + 2*num_clients_per_server)` 估算 |
| `--analyze` | 从 `batch_test_results.json` 生成 analysis + summary CSV |
| `--plot` | 配合 `--analyze` 出图 |
| `--plot-only` | 只从 summary CSV 重画图 |
| `--run-dir PATH` | 指定结果目录；不传则新建 `./experiments/realworld_<timestamp>/` |
| `--num-runs N` | 覆盖 `NUM_RUNS_PER_POINT` |
| `--port-base N` | 覆盖 `PORT_BASE` |
| `--num-clients-per-server N` | 覆盖同名参数 |
| `--set-size N` | 覆盖 `SET_SIZE`；不传 = 1 doc/client |
| `--seed-values 6 7 8` | 覆盖 `SEED_VALUES` |
| `--verbose` | 打 INFO 级日志 |

## 输出

所有产物在 `./experiments/realworld_<timestamp>/` 下:

- `results/batch_test_results.json` — 原始每次运行结果，可断点续跑
- `results/analysis_results.json` — 每个 (k, seed, mode) 统计 (去首尾 5% 后的 mean/median/std)
- `results/realworld_errorvsepsilon_summary.csv` — 同上扁平表
- `plots/realworld_error_vs_epsilon.{png,pdf}` — error vs $1/\sqrt{k}$ 曲线图
- `data/test_<id>/` — 单次生成的 client 数据，跑完该 k 后自动清掉

## 注意

- 脚本启动会 `pkill -f psi_server / psi_client` 清残留进程，跑前确认本机没有别的 PSI 任务
- 断点续跑：用相同的 `--run-dir` 再跑一次，已完成的 (k, seed, mode) 会跳过
- `mom_k * mom_t` 不能超过 8192 (沿用全局限制)