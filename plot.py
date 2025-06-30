import os
import sys
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import gaussian_kde
import matplotlib.cm as cm

# 定义文件夹路径
default_prefix = '/home/lyd8051/psi-code/build/'
if len(sys.argv) > 1:
    log_subfolder = sys.argv[1]
else:
    log_subfolder = input("Enter the log name: ")
log_folder = os.path.join(default_prefix, log_subfolder)
output_folder = os.path.join(default_prefix, log_subfolder + '_output')
os.makedirs(output_folder, exist_ok=True)

# 获取所有文件路径
files = [os.path.join(log_folder, f) for f in os.listdir(log_folder) if f.endswith('.txt')]

# 提取 n, N, c 的值
def extract_n_N_c(filename):
    parts = filename.split('_')
    n = parts[1][1:]
    N = parts[2][1:]
    c = parts[3][1:].split('.')[0]
    return n, N, c

# 核密度估计并绘制分布
def plot_distribution(data, label, color):
    kde = gaussian_kde(data)
    x_vals = np.linspace(0, 2, 1000)
    pdf = kde(x_vals)
    plt.plot(x_vals, pdf, label=label, color=color)

# 分类存储数据
data_by_N_c = {}
data_by_n_c = {}
data_by_n_N = {}

# 遍历文件并读取数据
for file in files:
    n, N, c = extract_n_N_c(os.path.basename(file))
    data = np.loadtxt(file, delimiter=',')
    prg_data = data if data.ndim == 1 else data[:, 0]

    key_N_c = (N, c)
    data_by_N_c.setdefault(key_N_c, []).append((prg_data, n, N, c, os.path.basename(file)))

    key_n_c = (n, c)
    data_by_n_c.setdefault(key_n_c, []).append((prg_data, n, N, c, os.path.basename(file)))

    key_n_N = (n, N)
    data_by_n_N.setdefault(key_n_N, []).append((prg_data, n, N, c, os.path.basename(file)))

# n 不同，N、c 相同
for (N, c), datasets in data_by_N_c.items():
    plt.figure(figsize=(8, 6))
    colors_prg = cm.Oranges(np.linspace(0.4, 1, len(datasets)))
    datasets = sorted(datasets, key=lambda x: int(x[1]))
    for i, (prg_data, n, _, _, _) in enumerate(datasets):
        plot_distribution(prg_data, f'n={n} - PRG', colors_prg[i])
    plt.xlabel('Difference')
    plt.ylabel('Density')
    plt.title(f'PDF for N={N}, c={c} (vary n)')
    plt.legend()
    plt.grid()
    plt.xlim(0, 2)
    plt.savefig(os.path.join(output_folder, f'PDF_N{N}_c{c}.png'), dpi=300)
    plt.close()

# N 不同，n、c 相同
for (n, c), datasets in data_by_n_c.items():
    plt.figure(figsize=(8, 6))
    colors_prg = cm.Oranges(np.linspace(0.4, 1, len(datasets)))
    datasets = sorted(datasets, key=lambda x: int(x[2]))
    for i, (prg_data, _, N, _, _) in enumerate(datasets):
        plot_distribution(prg_data, f'N={N} - PRG', colors_prg[i])
    plt.xlabel('Difference')
    plt.ylabel('Density')
    plt.title(f'PDF for n={n}, c={c} (vary N)')
    plt.legend()
    plt.grid()
    plt.xlim(0, 2)
    plt.savefig(os.path.join(output_folder, f'PDF_n{n}_c{c}.png'), dpi=300)
    plt.close()

# c 不同，n、N 相同
for (n, N), datasets in data_by_n_N.items():
    plt.figure(figsize=(8, 6))
    colors_prg = cm.Oranges(np.linspace(0.4, 1, len(datasets)))
    datasets = sorted(datasets, key=lambda x: int(x[3]))
    for i, (prg_data, _, _, c, _) in enumerate(datasets):
        plot_distribution(prg_data, f'c={c} - PRG', colors_prg[i])
    plt.xlabel('Difference')
    plt.ylabel('Density')
    plt.title(f'PDF for n={n}, N={N} (vary c)')
    plt.legend()
    plt.grid()
    plt.xlim(0, 2)
    plt.savefig(os.path.join(output_folder, f'PDF_n{n}_N{N}.png'), dpi=300)
    plt.close()
