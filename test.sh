logdir='log_0703'
exper_n="6"
exper_N="12 14"
exper_c="6"
exper_T=100
MAX_THREADS=1

mkdir -p $logdir
running_jobs=0
# set-x

for expn in $exper_n
do
    for expN in $exper_N
    do
        for expc in $exper_c
        do
            (
                echo "Running with N=$expN n=$expn c=$expc ..."
                echo "" > $logdir/log_n${expn}_N${expN}_c${expc}.txt
                for T in $(seq 1 $exper_T)
                do
                    ./bin/psi_ca -p 1 --universal_set_size_bit=$expN --testmode=$logdir/log_n${expn}_N${expN}_c${expc}.txt & ./bin/psi_ca -p 2 --universal_set_size_bit=$expN --testmode=$logdir/log_n${expn}_N${expN}_c${expc}.txt
                    # ./psi_ca --psi_mode=prg_nondeter_naive --universal_set_size_bit=$expN --seed_size_bit=$expn --prg_dd=$expc --testmode=$logdir/log_n${expn}_N${expN}_c${expc}.txt
                    # ./psi_ca --universal_set_size_bit=$expN --seed_size_bit=$expn --prg_dd=$expc --mom_tt=1 --testmode=$logdir/log_n${expn}_N${expN}_c${expc}.txt
                done
                echo "Completed N=$expN n=$expn c=$expc"
            ) &

            running_jobs=$((running_jobs + 1))
            if [[ "$running_jobs" -ge "$MAX_THREADS" ]]; then
                wait -n
                running_jobs=$((running_jobs - 1))
            fi
        done
    done
done

wait
echo "All tests completed."

python3 ../plot.py $logdir
echo "Plotting completed."