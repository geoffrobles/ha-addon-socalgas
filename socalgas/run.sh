#!/bin/bash

while true
do
    echo "Running SoCalGas sync..."

    echo "MQTT_HOST=$MQTT_HOST"
    echo "EMAIL=$SOCALGAS_EMAIL"
    echo "MQTT_USER=$MQTT_USER"

    python3 /app/socalgas_api_slim.py

    echo "Sleeping 6 hours..."

    sleep 21600
done