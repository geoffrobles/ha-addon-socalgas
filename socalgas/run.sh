#!/usr/bin/with-contenv bashio

export SOCALGAS_EMAIL=$(bashio::config 'email')
export SOCALGAS_PASSWORD=$(bashio::config 'password')

export MQTT_HOST=$(bashio::config 'mqtt_host')
export MQTT_PORT=$(bashio::config 'mqtt_port')
export MQTT_USER=$(bashio::config 'mqtt_user')
export MQTT_PASSWORD=$(bashio::config 'mqtt_password')

python3 /app/socalgas_api_slim.py