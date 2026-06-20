## Data Published

The add-on publishes a JSON payload to:

```text
home/socalgas/total
```

Example payload:

```json
{
  "therms_to_date": 17,
  "projected_therms": 19,
  "projected_bill": 35.21,
  "cost_to_date": 31.50,
  "billing_cycle_start": "2026-05-01",
  "billing_cycle_end": "2026-06-03",
  "updated_at": "2026-05-31T14:33:09.585728"
}
```

### Field Definitions

| Field | Description |
|---------|-------------|
| therms_to_date | Current estimated gas usage for the billing cycle in Therms |
| projected_therms | Projected total gas usage for the billing cycle in Therms |
| cost_to_date | Current estimated charges accumulated so far |
| projected_bill | Projected bill amount at the end of the billing cycle |
| billing_cycle_start | Beginning of the current billing cycle |
| billing_cycle_end | End of the current billing cycle |
| updated_at | Timestamp when the data was retrieved |

---

# MQTT Sensors

Create the following MQTT sensors in Home Assistant.

```yaml
mqtt:
  sensor:
    - name: SoCalGas Therms To Date
      unique_id: socalgas_therms_to_date
      state_topic: home/socalgas/total
      value_template: "{{ value_json.therms_to_date }}"
      unit_of_measurement: "Therms"

    - name: SoCalGas Projected Therms
      unique_id: socalgas_projected_therms
      state_topic: home/socalgas/total
      value_template: "{{ value_json.projected_therms }}"
      unit_of_measurement: "Therms"

    - name: SoCalGas Cost To Date
      unique_id: socalgas_cost_to_date
      state_topic: home/socalgas/total
      value_template: "{{ value_json.cost_to_date }}"
      unit_of_measurement: "$"

    - name: SoCalGas Projected Bill
      unique_id: socalgas_projected_bill
      state_topic: home/socalgas/total
      value_template: "{{ value_json.projected_bill }}"
      unit_of_measurement: "$"

    - name: SoCalGas Billing Cycle Start
      unique_id: socalgas_billing_cycle_start
      state_topic: home/socalgas/total
      value_template: "{{ value_json.billing_cycle_start }}"

    - name: SoCalGas Billing Cycle End
      unique_id: socalgas_billing_cycle_end
      state_topic: home/socalgas/total
      value_template: "{{ value_json.billing_cycle_end }}"
```

---

# Optional: Convert Therms to CCF

Home Assistant's Energy Dashboard does not currently accept Therms for gas usage. It requires one of the following units:

- CCF
- ft³
- L
- MCF
- m³

Your SoCalGas bill shows that Therms are calculated using a BTU factor:

```text
Therms = CCF × BTU Factor
```

For the sample bill used during development:

```text
BTU Factor = 1.038
```

To estimate CCF values, create the following template sensors.

## Projected Usage in CCF

```yaml
template:
  - sensor:
      - name: SoCalGas Projected CCF
        unique_id: socalgas_projected_ccf
        unit_of_measurement: "CCF"
        state: >
          {{ (states('sensor.socalgas_projected_therms') | float / 1.038) | round(2) }}
```

## Usage To Date in CCF

```yaml
template:
  - sensor:
      - name: SoCalGas Usage To Date CCF
        unique_id: socalgas_usage_to_date_ccf
        unit_of_measurement: "CCF"
        state: >
          {{ (states('sensor.socalgas_therms_to_date') | float / 1.038) | round(2) }}
```

> Note: The BTU factor may vary over time. The add-on publishes the raw Therm values returned by SoCalGas and leaves any conversions to Home Assistant.

---

# Example Dashboard Cards

Useful entities to add to a dashboard:

- `sensor.socalgas_therms_to_date`
- `sensor.socalgas_projected_therms`
- `sensor.socalgas_cost_to_date`
- `sensor.socalgas_projected_bill`
- `sensor.socalgas_usage_to_date_ccf`
- `sensor.socalgas_projected_ccf`

These provide a quick view of:

- Current gas usage
- Projected end-of-cycle usage
- Current charges
- Estimated bill amount
- Approximate CCF values for Energy Dashboard integrations

---

# Update Frequency

The add-on retrieves fresh data every 6 hours and republishes the latest values to MQTT.