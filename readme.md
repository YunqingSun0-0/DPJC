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