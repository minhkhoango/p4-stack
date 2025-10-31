# p4_stack/state.py
import json
import os
from dataclasses import dataclass, asdict
from typing import List, Optional

# Per the plan, the state file is in the CWD.
STATE_FILE = ".p4-stack-state.json"

@dataclass
class UpdateState:
    """
    Stores the state of a resumable 'update' operation.
    Corresponds to the plan's .p4-stack-state.json spec.
    """
    base_cl: str
    stack_to_update: List[str]
    rebased_cls: List[str]
    current_operation: str = "update"
    conflict_cl: Optional[str] = None

def save_state(state: UpdateState) -> None:
    """Saves the current state to the JSON file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(asdict(state), f, indent=2)
    except IOError as e:
        print(f"Error: Could not write state file {STATE_FILE}: {e}")

def load_state() -> Optional[UpdateState]:
    """Loads state from the JSON file. Returns None if not found."""
    if not os.path.exists(STATE_FILE):
        return None
        
    try:
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            return UpdateState(**data)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error: Could not read or parse state file {STATE_FILE}: {e}")
        return None
    
def clear_state() -> None:
    """Removes the state file on success."""
    if os.path.exists(STATE_FILE):
        try:
            os.remove(STATE_FILE)
        except IOError as e:
            print(f"Warning: Could not remove state file {STATE_FILE}: {e}")