#!/bin/bash

# FHE Performance Test Runner Script
# This script provides an easy way to run FHE performance tests

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if executables exist
check_executables() {
    print_status "Checking if required executables exist..."
    
    local missing_files=()
    
    if [ ! -f "./build/bin/gendata" ]; then
        missing_files+=("./build/bin/gendata")
    fi
    
    if [ ! -f "./build/bin/psi_server" ]; then
        missing_files+=("./build/bin/psi_server")
    fi
    
    if [ ! -f "./build/bin/psi_client" ]; then
        missing_files+=("./build/bin/psi_client")
    fi
    
    if [ ${#missing_files[@]} -ne 0 ]; then
        print_error "Missing required executables:"
        for file in "${missing_files[@]}"; do
            echo "  - $file"
        done
        print_error "Please build the project first:"
        echo "  cd build && make -j\$(nproc)"
        exit 1
    fi
    
    print_success "All required executables found"
}

# Function to kill existing processes
cleanup_processes() {
    print_status "Cleaning up existing PSI processes..."
    
    if pkill -f psi_server 2>/dev/null; then
        print_warning "Killed existing psi_server processes"
    fi
    
    if pkill -f psi_client 2>/dev/null; then
        print_warning "Killed existing psi_client processes"
    fi
    
    sleep 1
}

# Function to check Python dependencies
check_python_deps() {
    print_status "Checking Python dependencies..."
    
    local missing_deps=()
    
    if ! python3 -c "import numpy" 2>/dev/null; then
        missing_deps+=("numpy")
    fi
    
    if ! python3 -c "import matplotlib" 2>/dev/null; then
        missing_deps+=("matplotlib")
    fi
    
    if ! python3 -c "import pandas" 2>/dev/null; then
        missing_deps+=("pandas")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        print_error "Missing Python dependencies:"
        for dep in "${missing_deps[@]}"; do
            echo "  - $dep"
        done
        print_error "Please install missing dependencies:"
        echo "  pip install ${missing_deps[*]}"
        exit 1
    fi
    
    print_success "All Python dependencies found"
}

# Function to run single test
run_single_test() {
    print_status "Running single FHE test..."
    
    if python3 fhe_test_single.py; then
        print_success "Single FHE test completed successfully"
    else
        print_error "Single FHE test failed"
        exit 1
    fi
}

# Function to run batch tests
run_batch_tests() {
    print_status "Running FHE batch performance tests..."
    
    local args=""
    if [ "$VERBOSE" = "true" ]; then
        args="--verbose"
    fi
    
    if python3 fhe_performance_test_batch.py $args; then
        print_success "FHE batch tests completed successfully"
    else
        print_error "FHE batch tests failed"
        exit 1
    fi
}

# Function to generate plots only
generate_plots() {
    print_status "Generating plots from existing results..."
    
    if python3 fhe_performance_test_batch.py --plot-only; then
        print_success "Plots generated successfully"
    else
        print_error "Failed to generate plots"
        exit 1
    fi
}

# Function to show help
show_help() {
    echo "FHE Performance Test Runner"
    echo ""
    echo "Usage: $0 [OPTIONS] COMMAND"
    echo ""
    echo "Commands:"
    echo "  single     Run a single FHE test to verify functionality"
    echo "  batch      Run full batch performance tests"
    echo "  plots      Generate plots from existing results"
    echo "  clean      Clean up test data and processes"
    echo "  help       Show this help message"
    echo ""
    echo "Options:"
    echo "  --verbose  Enable verbose output"
    echo ""
    echo "Examples:"
    echo "  $0 single              # Run single test"
    echo "  $0 batch --verbose     # Run batch tests with verbose output"
    echo "  $0 plots               # Generate plots only"
}

# Function to clean up
cleanup() {
    print_status "Cleaning up test data and processes..."
    
    cleanup_processes
    
    if [ -d "./fhe_performance_data" ]; then
        rm -rf ./fhe_performance_data
        print_success "Removed test data directory"
    fi
    
    if [ -d "./test_fhe_single" ]; then
        rm -rf ./test_fhe_single
        print_success "Removed single test data directory"
    fi
    
    print_success "Cleanup completed"
}

# Main script logic
main() {
    # Parse command line arguments
    VERBOSE="false"
    COMMAND=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --verbose)
                VERBOSE="true"
                shift
                ;;
            single|batch|plots|clean|help)
                COMMAND="$1"
                shift
                ;;
            *)
                print_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
    
    if [ -z "$COMMAND" ]; then
        print_error "No command specified"
        show_help
        exit 1
    fi
    
    # Execute command
    case $COMMAND in
        single)
            check_executables
            check_python_deps
            cleanup_processes
            run_single_test
            ;;
        batch)
            check_executables
            check_python_deps
            cleanup_processes
            run_batch_tests
            ;;
        plots)
            check_python_deps
            generate_plots
            ;;
        clean)
            cleanup
            ;;
        help)
            show_help
            ;;
        *)
            print_error "Unknown command: $COMMAND"
            show_help
            exit 1
            ;;
    esac
}

# Run main function
main "$@"






