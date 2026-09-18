---
name: task_latency_check
status: active
tier: TIER_1
team: network
version: 1
created_at: 1789324906.3323712
updated_at: 1789760890.0071151
success_count: 0
fail_count: 0
flagged: false
recent_outcomes: 
tools: check_latency, list_tasks
---

## When to use

When managing tasks and needing to verify their latencies, especially for overdue tasks, to assess performance or schedule adjustments

## Steps

1. 1. Use check_latency to ping the host and retrieve latency statistics. 2. Use list_tasks to retrieve and sort tasks by due date to correlate latency data with task deadlines.
