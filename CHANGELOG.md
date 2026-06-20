## 1.1.0
- fix: validate usage payload by schema, not URL/key heuristics
    - Replace URL substring matching + "UsageSoFar" key check with
    is_usage_payload(), which validates the actual CostToDate fields
    the parser depends on — closes a gap where capture and parse logic
    checked different things
    - Stop gating success on login_verified; usage_widget_data presence
    (already schema-validated) is sufficient proof of auth, and the
    old gate could false-fail if SoCalGas changes header behavior
    - Raise on non-numeric fields in build_payload instead of silently
    coercing to 0 (schema drift now fails loud, not silent)
    - Confirm MQTT publish actually delivered via is_published(), not
    just handshake completion
    - Stop logging partial AccessToken values in debug output
    - Add explicit timeout to page.goto()

## 1.0.18
- Fixed schema drift validation that could silently publish zero values
- Added MQTT publish delivery confirmation
- Removed unsafe AccessToken logging in debug mode

## 1.0.0
- Initial Commit