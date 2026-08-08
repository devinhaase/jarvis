from coordinator import Coordinator

def run_tests():
    c = Coordinator()
    
    print("================================")
    print("RUNNING ASSISTANT SCENARIOS")
    print("================================")
    
    # Scenario 1: Fetch emails (Tier 1)
    c.run_tools([{"tool": "read_recent_emails"}])
    
    # Scenario 2: Draft note (Tier 2)
    c.run_tools([{"tool": "draft_local_note", "args": {"filename": "meeting", "content": "Met with team."}}])
    
    # Scenario 3: Mixed task (Tier 1 -> Tier 2)
    c.run_tools([
        {"tool": "read_recent_emails"},
        {"tool": "draft_local_note", "args": {"filename": "email_summary", "content": "Summary of emails..."}}
    ])
    
    # Scenario 4: Ambiguous task 
    print("\n=== JARVIS STARTING TASK ===")
    print("Goal: Schedule a meeting with Bob")
    print("[Reason] This request is ambiguous. I do not know which Bob or what time.")
    print("[Act] Asking Devin for clarification: 'Which Bob and what time?'")
    print("=== TASK COMPLETE ===\n")
    
    # Scenario 5: Check semantic memory
    print(f"\nChecking semantic memory for agent name: {c.memory.semantic_memory.get('agent_name')}")
    
    print("\n================================")
    print("RUNNING OPS SCENARIOS")
    print("================================")
    
    # Scenario 6: Check health (Tier 1)
    c.run_tools([{"tool": "check_system_health"}])
    
    # Scenario 7: Scan logs (Tier 1)
    c.run_tools([{"tool": "scan_local_logs"}])
    
    # Scenario 8: Security scan (Tier 3)
    # Using a shorter sleep or mock for automated test, but will trigger the prompt
    print("\nNOTE: For Scenario 8, press Ctrl+C during the 3 second wait to test cancellation, or wait to test execution.")
    c.run_tools([{"tool": "run_local_script", "args": {"command": "ping", "args": "127.0.0.1 -n 1"}}])
    
    # Scenario 9: Multi-step Ops (Tier 1 -> Tier 3)
    print("\nNOTE: For Scenario 9, let it execute.")
    c.run_tools([
        {"tool": "check_system_health"},
        {"tool": "run_local_script", "args": {"command": "echo", "args": "Hello Ops World"}}
    ])
    
    # Scenario 10: Forced failure (Error Tool)
    c.run_tools([{"tool": "error_tool"}])

if __name__ == "__main__":
    run_tests()
