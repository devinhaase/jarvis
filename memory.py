import json
import os
import time

class Memory:
    def __init__(self, data_dir="data"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.semantic_file = os.path.join(self.data_dir, "semantic_memory.json")
        self.episodic_file = os.path.join(self.data_dir, "episodic_memory.jsonl")
        
        self.working_memory = {}
        self._init_semantic()

    def _init_semantic(self):
        if not os.path.exists(self.semantic_file):
            default = {
                "user_name": "Devin",
                "agent_name": "Jarvis",
                "facts": [],
                "projects": [],
                "preferences": {}
            }
            with open(self.semantic_file, 'w') as f:
                json.dump(default, f, indent=4)
        
        with open(self.semantic_file, 'r') as f:
            self.semantic_memory = json.load(f)

    def get_semantic_context(self) -> str:
        """Return a formatted string of long-term memory for injection into LLM context."""
        mem = self.semantic_memory
        lines = []

        if mem.get("facts"):
            lines.append("Ongoing facts Jarvis should know:")
            for fact in mem["facts"]:
                lines.append(f"  - {fact}")

        if mem.get("projects"):
            lines.append("Active projects:")
            for proj in mem["projects"]:
                lines.append(f"  - {proj}")

        if mem.get("preferences"):
            lines.append("Devin's known preferences:")
            for k, v in mem["preferences"].items():
                lines.append(f"  - {k}: {v}")

        return "\n".join(lines) if lines else ""

    def update_semantic(self, key: str, value):
        """Write a key/value pair into semantic memory and persist to disk."""
        self.semantic_memory[key] = value
        with open(self.semantic_file, 'w') as f:
            json.dump(self.semantic_memory, f, indent=4)

    def append_fact(self, fact: str):
        """Append a single fact string to the facts list."""
        facts = self.semantic_memory.setdefault("facts", [])
        if fact not in facts:
            facts.append(fact)
            with open(self.semantic_file, 'w') as f:
                json.dump(self.semantic_memory, f, indent=4)

    def log_episode(self, action, result, tier):
        episode = {
            "timestamp": time.time(),
            "action": action,
            "result": result,
            "tier": tier
        }
        with open(self.episodic_file, 'a') as f:
            f.write(json.dumps(episode) + "\n")

    def get_recent_episodes(self, n=5) -> list:
        """Return the last n episodic memory entries."""
        try:
            with open(self.episodic_file, 'r') as f:
                lines = f.readlines()
            return [json.loads(l) for l in lines[-n:]]
        except Exception:
            return []

    def update_working_memory(self, key, value):
        self.working_memory[key] = value
