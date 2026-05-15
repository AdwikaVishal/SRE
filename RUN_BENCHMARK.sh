#!/bin/bash

# Exact benchmark commands from the specification

echo "Step 1: Clear Python cache..."
find "/Users/apple/Downloads/Mini_Anvil copy" -name "*.pyc" -delete
echo "✅ Python cache cleared"

echo ""
echo "Step 2: Navigate to benchmark directory..."
cd "/Users/apple/Downloads/Mini_Anvil copy/Anvil-P-E/bench-p02-context"
echo "✅ In $(pwd)"

echo ""
echo "Step 3: Set PYTHONPATH..."
export PYTHONPATH="$PWD/../.."
echo "✅ PYTHONPATH=$PYTHONPATH"

echo ""
echo "Step 4: Run benchmark..."
echo "   Mode: fast (no LLM)"
echo "   Seeds: 42, 101"
echo "   Services: 30"
echo "   Days: 14"
echo ""
python run.py --adapter adapters.engine:Engine --mode fast \
  --seeds 42 101 --n-services 30 --days 14 \
  --out ../../report.json

echo ""
echo "Step 5: View results..."
echo "=================================="
cat ../../report.json | grep -A 6 aggregated
echo "=================================="
echo ""
echo "✅ Benchmark complete! Results in: /Users/apple/Downloads/Mini_Anvil copy/report.json"
