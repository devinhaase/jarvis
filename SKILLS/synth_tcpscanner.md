---
name: synth: tcpscanner
status: proposed
tier: TIER_4
team: hacking
version: 1
created_at: 1786202799.7826958
updated_at: 1786202799.7826958
success_count: 0
fail_count: 0
flagged: false
recent_outcomes: 
tools: run_synthesized_script
---

## When to use

A lightweight TCP port scanner that checks ports 20-25 and 80 on the target host. (goal: Write a lightweight TCP port scanner for an authorized target, scanning ports 20-25 and 80.)

## Steps

1. Review the generated script at data\synthesized_tools\tcpscanner_20260808T152639Z.py before approving.
2. Run via run_synthesized_script(script_path='data\synthesized_tools\tcpscanner_20260808T152639Z.py', target=<authorized target>) — Tier 4, re-checks authorization on every call regardless of approval history.
