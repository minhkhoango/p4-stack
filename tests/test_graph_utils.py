# tests/test_graph_utils.py
import pytest
from p4_stack.graph_utils import parse_depends_on, build_stack_graph

# --- Mocks ---

# Simulates p4.run('changes', ...) output
MOCK_RAW_CHANGES = [
    {
        'change': '19',
        'desc': 'feat: Add new_feature\n\nDepends-On: 18\n', # Parent 18 is submitted
    },
    {
        'change': '20',
        'desc': 'fix: Correct logic in new_feature\n\nDepends-On: 19\n',
    },
    {
        'change': '21',
        'desc': 'docs: Add docs for new_feature\n\nDepends-On: 20\n',
    },
    {
        'change': '23',
        'desc': 'fix: Separate bug in login service\n\n(No dependency)\n',
    },
]

# --- Unit Tests for parse_depends_on ---

@pytest.mark.unit
@pytest.mark.parametrize("desc, expected", [
    ("Hello world\nDepends-On: 12345\nMore text", "12345"),
    ("depends-on: 54321", "54321"),
    ("Depends-On:    999", "999"),
    ("No dependency here", None),
    ("Depends-On: abc (invalid)", None),
])
def test_parse_depends_on(desc: str, expected: str | None) -> None:
    """Tests the regex parsing for the Depends-On tag."""
    assert parse_depends_on(desc) == expected
    
# --- Unit Tests for build_stack_graph ---

@pytest.mark.unit
def test_build_stack_graph_empty() -> None:
    """Tests that an empty list of changes returns an empty graph."""
    assert build_stack_graph([]) == []

@pytest.mark.unit
def test_build_stack_graph_full() -> None:
    """Tests building a complex graph with multiple roots and children."""
    graph = build_stack_graph(MOCK_RAW_CHANGES)
    
    # We expect two roots: 19 and 23
    assert len(graph) == 2
    root_cls = {node.cl_num for node in graph}
    assert root_cls == {"19", "23"}
    
    # Find the '19' root
    root_19 = next(n for n in graph if n.cl_num == '19')
    
    # Check stack 19 -> 20 -> 21
    assert len(root_19.children) == 1
    
    child_20 = root_19.children[0]
    assert child_20.cl_num == '20'
    assert child_20.parent_cl == '19'
    assert len(child_20.children) == 1
    
    child_21 = child_20.children[0]
    assert child_21.cl_num == '21'
    assert child_21.parent_cl == '20'
    assert len(child_21.children) == 0
    
    # Find the '23' root
    root_23 = next(n for n in graph if n.cl_num == '23')
    assert root_23.parent_cl is None
    assert len(root_23.children) == 0