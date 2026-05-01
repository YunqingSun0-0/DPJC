#!/usr/bin/env python3
"""
Estimate weight_scale_div vectors for FHE weighted tests.

Default model matches fhe_test_single.py auto_scale (with small-intersection safety),
and extends it with an optional conservative client-overlap mode.
"""

import argparse
import csv
import math
import os
from typing import List


def next_power_of_two(x: float) -> int:
    n = int(math.ceil(x))
    if n <= 1:
        return 1
    return 1 << ((n - 1).bit_length())


def estimate_scale_div(
    set_size_bit: int,
    num_clients_per_server: int,
    max_weight: int,
    plain_modulus_bit: int,
    mom_k: int,
    mode: str,
) -> int:
    set_size = 1 << set_size_bit
    intersection_est = float(max(1, set_size // 2))

    if mode == "random":
        overlap_factor = 1.0
    elif mode == "worst_overlap":
        overlap_factor = float(max(1, num_clients_per_server))
    else:
        raise ValueError(f"Unsupported mode: {mode}")

    effective_intersection = intersection_est * overlap_factor
    mean_weight = (float(max_weight) + 1.0) / 2.0
    expected_weighted_sum = effective_intersection * mean_weight * mean_weight
    expected_bucket_sum = expected_weighted_sum * float(max(1, mom_k))

    # Same idea as fhe_test_single.py: increase guard when intersection is tiny.
    tail_safety = max(1.0, 2.0 / math.sqrt(effective_intersection))
    expected_bucket_sum *= tail_safety

    plain_modulus_est = float((1 << max(2, plain_modulus_bit)) - 1)
    target_bucket = plain_modulus_est / 2.0
    required_from_bucket = math.sqrt(
        max(1.0, expected_bucket_sum / max(1.0, target_bucket))
    )

    required_from_encoding = float(max_weight) / max(1.0, plain_modulus_est - 1.0)
    required = max(1.0, required_from_bucket, required_from_encoding)
    return next_power_of_two(required)


def parse_weights(raw: str) -> List[int]:
    vals: List[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        vals.append(int(token))
    if not vals:
        raise ValueError("No max_weight values provided")
    return vals


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Generate scale vectors for (set_size_bit, num_clients_per_server, max_weight)."
    )
    parser.add_argument("--set-size-bit-min", type=int, default=1)
    parser.add_argument("--set-size-bit-max", type=int, default=24)
    parser.add_argument("--clients-min", type=int, default=1)
    parser.add_argument("--clients-max", type=int, default=20)
    parser.add_argument(
        "--max-weights",
        type=str,
        default="8192,1000000,8000000",
        help="Comma-separated max_weight values (e.g. 4096,8192,1000000,8000000)",
    )
    parser.add_argument("--plain-modulus-bit", type=int, default=24)
    parser.add_argument("--mom-k", type=int, default=400)
    parser.add_argument(
        "--mode",
        choices=["random", "worst_overlap"],
        default="random",
        help=(
            "random: current gendata random model (client count does not change overlap); "
            "worst_overlap: conservative client aggregation overlap ~= num_clients_per_server"
        ),
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default=os.path.join(script_dir, "scale_vector.csv"),
    )
    args = parser.parse_args()

    if args.set_size_bit_min < 1 or args.set_size_bit_max < args.set_size_bit_min:
        raise ValueError("Invalid set_size_bit range")
    if args.clients_min < 1 or args.clients_max < args.clients_min:
        raise ValueError("Invalid clients range")

    max_weights = parse_weights(args.max_weights)

    with open(args.output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "mode",
                "set_size_bit",
                "set_size",
                "num_clients_per_server",
                "max_weight",
                "recommended_weight_scale_div",
            ]
        )

        for b in range(args.set_size_bit_min, args.set_size_bit_max + 1):
            set_size = 1 << b
            for c in range(args.clients_min, args.clients_max + 1):
                for w in max_weights:
                    scale = estimate_scale_div(
                        set_size_bit=b,
                        num_clients_per_server=c,
                        max_weight=w,
                        plain_modulus_bit=args.plain_modulus_bit,
                        mom_k=args.mom_k,
                        mode=args.mode,
                    )
                    writer.writerow([args.mode, b, set_size, c, w, scale])

    print(f"Wrote: {args.output_csv}")


if __name__ == "__main__":
    main()
