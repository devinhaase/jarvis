---
name: system_health_check
status: active
tier: TIER_1
team: it
version: 1
created_at: 1786237348.0411735
updated_at: 1786238123.615814
success_count: 0
fail_count: 0
flagged: false
recent_outcomes: 
tools: check_system_health, get_security_posture
---

## When to use

When assessing the overall system health, including both performance and security aspects, this combination of tools is used.

## Steps

1. 1. Check system CPU usage using check_system_health. 2. Evaluate local machine security posture (Defender, firewall, patches, listening services) using get_security_posture.
