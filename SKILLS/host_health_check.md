---
name: host_health_check
status: active
tier: TIER_1
team: network
version: 1
created_at: 1786980162.0962322
updated_at: 1789760887.9915228
success_count: 0
fail_count: 0
flagged: false
recent_outcomes: 
tools: check_latency, check_system_health
---

## When to use

When assessing the performance and stability of a server or network host, this combination of tools provides insights into both network latency and system CPU usage.

## Steps

1. 1. Run check_latency to measure minimum, average, and maximum latency across multiple hosts. 2. Execute check_system_health to monitor CPU usage and identify potential performance bottlenecks.
