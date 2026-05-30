#!/bin/bash

export SOCALGAS_EMAIL=$(bashio::config 'email')
export SOCALGAS_PASSWORD=$(bashio::config 'password')

export MQTT_HOST=$(bashio::config 'mqtt_host')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password')

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