#!/usr/bin/env bash
set -e

cmake .
make -j$(nproc)

if [ ! -f uci_words/docword.nytimes.txt ]; then
  mkdir -p uci_words
  curl -L -o uci_words/docword.nytimes.txt.gz \
    https://archive.ics.uci.edu/ml/machine-learning-databases/bag-of-words/docword.nytimes.txt.gz
  gunzip uci_words/docword.nytimes.txt.gz
fi

python3 accuracy_test_realworld.py --parallel --set-size 262144
python3 accuracy_test_batch.py --param-mode seed_optimization --seed-optimization-parallel
python3 accuracy_test_batch.py --param-mode errorvsepsilon --errorvsepsilon-parallel
python3 fhe_test_single.py
./fhe_run_seed_tests.sh --seed-bits "7 8 9 10"
./fhe_run_client_compute_tests.sh 5
./fhe_run_agg_time.sh
