#!/bin/bash

while true
do
    echo "Running SoCalGas sync..."

    python3 /app/socalgas_api_slim.py

    echo "Sleeping 6 hours..."

    sleep 21600
done