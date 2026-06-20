# SoCalGas Usage Importer

Scrapes your SoCalGas billing/usage data and publishes it to MQTT for Home Assistant.

## Installation

1. Add this repository to your Supervisor add-on store
2. Install "SoCalGas Usage Importer"
3. Configure your credentials and MQTT broker below
4. Start the add-on

## Configuration

Example:

```yaml
email: "you@example.com"
password: "your-password"
mqtt_host: "192.168.1.x"
mqtt_port: 1883
mqtt_user: "mqtt-user"
mqtt_password: "your-mqtt-password"
mqtt_topic: "home/socalgas/total"
debug: false
```

### Option: `email`
Your SoCalGas account email.

### Option: `password`
Your SoCalGas account password.

## Sensors

This add-on publishes to the configured MQTT topic with the following payload fields: `therms_to_date`, `projected_therms`, `projected_bill`, `cost_to_date`, `billing_cycle_start`, `billing_cycle_end`, `updated_at`.