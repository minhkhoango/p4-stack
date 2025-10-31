# p4_stack/graph_utils.py
from __future__ import annotations

import re
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

# Regex to find the 'Depends-On: 12345' tag
# Per plan: case-insensitive, allows for whitespace
DEPENDS_ON_RE = re.compile(r'Depends-On:\s*(\d+)', re.IGNORECASE)

@dataclass
class StackedChange:
    """
    Represents a single changelist in a dependency graph.
    """
    cl_num: str
    description: str
    parent_cl: Optional[str] = None
    children: List[StackedChange] = field(default_factory=lambda: [])
    
    @property
    def short_desc(self) -> str:
        """Returns the first line of the description."""
        # Split on newline, get first line, strip whitespace
        return self.description.split('\n', 1)[0].strip()
    
def parse_depends_on(description: str) -> Optional[str]:
    """
    Finds the parent CL number in a description string.
    Returns CL number as a string, or None.
    """
    match = DEPENDS_ON_RE.search(description)
    if match:
        return match.group(1)
    return None

def build_stack_graph(
    raw_changes: List[Dict[str, Any]]
) -> List[StackedChange]:
    """Builds a forest (list of trees) of stacked changes."""
    
    changes_map: Dict[str, StackedChange] = {}
    
    # --- First pass: Create all nodes ---
    for change in raw_changes:
        cl_num = change.get('change')
        desc = change.get('desc', '')
        if not cl_num:
            continue
        
        parent_cl = parse_depends_on(desc)
        stacked_change = StackedChange(
            cl_num=cl_num,
            description=desc,
            parent_cl=parent_cl
        )
        changes_map[cl_num] = stacked_change
        
    # --- Second pass: Link nodes ---
    root_nodes: List[StackedChange] = []
    
    for cl_num, change_node in changes_map.items():
        parent_cl_num = change_node.parent_cl
        
        if parent_cl_num and parent_cl_num in changes_map:
            parent_node = changes_map[parent_cl_num]
            parent_node.children.append(change_node)
        else:
            root_nodes.append(change_node)
            
    # --- Sort for consistent display ---
    for change in changes_map.values():
        change.children.sort(key=lambda c: int(c.cl_num))
    root_nodes.sort(key=lambda c: int(c.cl_num))
    
    return root_nodes

def _get_all_nodes_map(
    roots: List[StackedChange]
) -> Dict[str, StackedChange]:
    """Returns a flat map of {cl_num: StackedChange} for all nodes."""
    all_nodes: Dict[str, StackedChange] = {}
    stack = list(roots)
    while stack:
        node = stack.pop()
        all_nodes[node.cl_num] = node
        stack.extend(node.children)
    return all_nodes

def find_stack_tip(roots: List[StackedChange]) -> Optional[str]:
    """
    Finds the "tip" of the stack, defined as the highest-numbered
    CL in the entire graph of pending changes.
    """
    all_nodes = _get_all_nodes_map(roots)
    if not all_nodes:
        return None
    
    # Find the CL with the highest number
    return max(all_nodes.keys(), key=int)

def set_depends_on(description: str, new_parent_cl: str) -> str:
    """
    Robustly adds or *replaces* the 'Depends-On:' tag in a description.
    """
    # First, remove any existing 'Depends-On' tag
    clean_desc = DEPENDS_ON_RE.sub("", description).strip()
    
    # Add the new tag
    return f"{clean_desc}\n\nDepends-On: {new_parent_cl}"