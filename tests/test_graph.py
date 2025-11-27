"""
Pytest tests for p4_stack.core.graph module.

Tests the dependency graph building and traversal logic for changelists.
"""
import pytest
from unittest.mock import Mock
from p4_stack.core.graph import (
    build_stack_graph,
    get_stack_from_base,
    get_stack_for_cl,
    get_changelist_status,
    DEPENDS_ON_RE,
)
from p4_stack.core.p4_actions import P4OperationError


class TestDependsOnRegex:
    """Test the DEPENDS_ON_RE regex pattern."""
    
    def test_regex_matches_depends_on_format(self):
        """Should match 'Depends-On: <number>' format."""
        desc = "Some description\n\nDepends-On: 123\n\nMore text"
        match = DEPENDS_ON_RE.search(desc)
        assert match is not None
        assert match.group(1) == "123"
    
    def test_regex_extracts_correct_number(self):
        """Should extract the correct CL number."""
        desc = "Depends-On: 9999"
        match = DEPENDS_ON_RE.search(desc)
        assert match is not None
        assert match.group(1) == "9999"
    
    def test_regex_no_match_without_depends_on(self):
        """Should not match descriptions without Depends-On."""
        desc = "Just a normal description without any dependencies"
        match = DEPENDS_ON_RE.search(desc)
        assert match is None
    
    def test_regex_handles_whitespace_variations(self):
        """Should handle various whitespace patterns."""
        test_cases = [
            "Depends-On: 100",
            "Depends-On:  100",
            "Depends-On:\t100",
        ]
        for desc in test_cases:
            match = DEPENDS_ON_RE.search(desc)
            assert match is not None


class TestBuildStackGraph:
    """Test the build_stack_graph function."""
    
    def test_build_stack_graph_single_cl_no_deps(self):
        """Should handle a single CL with no dependencies."""
        mock_p4 = Mock()
        mock_p4.run_changes.return_value = [
            {
                'change': '100',
                'desc': 'First changelist',
                'user': 'testuser',
                'client': 'test_client',
            }
        ]
        
        graph, child_to_parent, all_pending = build_stack_graph(mock_p4)
        
        assert graph == {}
        assert child_to_parent == {}
        assert all_pending == {100}
    
    def test_build_stack_graph_linear_dependency_chain(self):
        """Should build a linear dependency chain correctly."""
        mock_p4 = Mock()
        mock_p4.run_changes.return_value = [
            {'change': '100', 'desc': 'Root CL', 'user': 'testuser'},
            {'change': '101', 'desc': 'Child\n\nDepends-On: 100', 'user': 'testuser'},
            {'change': '102', 'desc': 'Grandchild\n\nDepends-On: 101', 'user': 'testuser'},
        ]
        
        graph, child_to_parent, all_pending = build_stack_graph(mock_p4)
        
        assert graph[100] == [101]
        assert graph[101] == [102]
        assert child_to_parent[101] == 100
        assert child_to_parent[102] == 101
        assert all_pending == {100, 101, 102}
    
    def test_build_stack_graph_multiple_children(self):
        """Should handle a CL with multiple child CLs."""
        mock_p4 = Mock()
        mock_p4.run_changes.return_value = [
            {'change': '100', 'desc': 'Root CL', 'user': 'testuser'},
            {'change': '101', 'desc': 'Child 1\n\nDepends-On: 100', 'user': 'testuser'},
            {'change': '102', 'desc': 'Child 2\n\nDepends-On: 100', 'user': 'testuser'},
        ]
        
        graph, child_to_parent, all_pending = build_stack_graph(mock_p4)
        
        assert set(graph[100]) == {101, 102}
        assert child_to_parent[101] == 100
        assert child_to_parent[102] == 100
        assert all_pending == {100, 101, 102}
    
    def test_build_stack_graph_p4_error(self):
        """Should raise P4OperationError when p4 command fails."""
        mock_p4 = Mock()
        mock_p4.run_changes.side_effect = Exception("Connection failed")
        
        with pytest.raises(P4OperationError, match="Failed to fetch pending changelists"):
            build_stack_graph(mock_p4)
    
    def test_build_stack_graph_empty_changelist(self):
        """Should handle when no pending changelists exist."""
        mock_p4 = Mock()
        mock_p4.run_changes.return_value = []
        
        graph, child_to_parent, all_pending = build_stack_graph(mock_p4)
        
        assert graph == {}
        assert child_to_parent == {}
        assert all_pending == set()


class TestGetStackFromBase:
    """Test the get_stack_from_base function."""
    
    def test_get_stack_from_base_single_node(self):
        """Should return single node when it has no children."""
        graph: dict[int, list[int]] = {}
        result = get_stack_from_base(100, graph)
        assert result == [100]
    
    def test_get_stack_from_base_linear_chain(self):
        """Should return stack in parent-first order for linear chain."""
        graph = {
            100: [101],
            101: [102],
        }
        result = get_stack_from_base(100, graph)
        assert result == [100, 101, 102]
    
    def test_get_stack_from_base_from_middle(self):
        """Should return stack starting from middle of chain."""
        graph = {
            100: [101],
            101: [102],
        }
        result = get_stack_from_base(101, graph)
        assert result == [101, 102]
    
    def test_get_stack_from_base_branching_tree(self):
        """Should traverse branching tree in BFS order."""
        graph = {
            100: [101, 102],
            101: [103],
            102: [104],
        }
        result = get_stack_from_base(100, graph)
        # BFS order: 100, then 101 and 102, then 103 and 104
        assert result[0] == 100
        assert set(result[1:3]) == {101, 102}
        assert set(result[3:5]) == {103, 104}
    
    def test_get_stack_from_base_not_found_returns_base(self):
        """Should return just the base CL if not in graph."""
        graph = {100: [101]}
        result = get_stack_from_base(999, graph)
        assert result == [999]


class TestGetStackForCl:
    """Test the get_stack_for_cl function."""
    
    def test_get_stack_for_cl_root_only(self):
        """Should return root CL when it has no parent."""
        child_to_parent: dict[int, int] = {}
        result = get_stack_for_cl(100, child_to_parent)
        assert result == [100]
    
    def test_get_stack_for_cl_linear_chain(self):
        """Should return full stack from root to tip."""
        child_to_parent = {
            101: 100,
            102: 101,
        }
        result = get_stack_for_cl(102, child_to_parent)
        assert result == [100, 101, 102]
    
    def test_get_stack_for_cl_middle_of_chain(self):
        """Should return stack from root to middle CL."""
        child_to_parent = {
            101: 100,
            102: 101,
        }
        result = get_stack_for_cl(101, child_to_parent)
        assert result == [100, 101]
    
    def test_get_stack_for_cl_deep_chain(self):
        """Should handle deep dependency chains."""
        child_to_parent = {
            101: 100,
            102: 101,
            103: 102,
            104: 103,
        }
        result = get_stack_for_cl(104, child_to_parent)
        assert result == [100, 101, 102, 103, 104]


class TestGetChangelistStatus:
    """Test the get_changelist_status function."""
    
    def test_get_changelist_status_pending(self):
        """Should return '(pending)' for pending changelists."""
        mock_p4 = Mock()
        mock_p4.run.return_value = [
            {
                'change': '100',
                'status': 'pending',
                'desc': 'Test CL',
            }
        ]
        
        result = get_changelist_status(mock_p4, 100)
        assert result == "(pending)"
    
    def test_get_changelist_status_submitted(self):
        """Should return '(submitted)' for submitted changelists."""
        mock_p4 = Mock()
        mock_p4.run.return_value = [
            {
                'change': '100',
                'status': 'submitted',
                'desc': 'Test CL',
            }
        ]
        
        result = get_changelist_status(mock_p4, 100)
        assert result == "(submitted)"
    
    def test_get_changelist_status_case_insensitive(self):
        """Should handle status field case-insensitively."""
        mock_p4 = Mock()
        mock_p4.run.return_value = [
            {
                'change': '100',
                'status': 'PENDING',
                'desc': 'Test CL',
            }
        ]
        
        result = get_changelist_status(mock_p4, 100)
        assert result == "(pending)"
    
    def test_get_changelist_status_not_found(self):
        """Should return '(not found)' when CL doesn't exist."""
        mock_p4 = Mock()
        mock_p4.run.return_value = []
        
        result = get_changelist_status(mock_p4, 999)
        assert result == "(not found)"
    
    def test_get_changelist_status_error_handling(self):
        """Should return '(not found)' on P4 errors."""
        mock_p4 = Mock()
        mock_p4.run.side_effect = Exception("P4 error")
        
        result = get_changelist_status(mock_p4, 100)
        assert result == "(not found)"
    
    def test_get_changelist_status_empty_result(self):
        """Should return '(not found)' for empty results."""
        mock_p4 = Mock()
        mock_p4.run.return_value = []
        
        result = get_changelist_status(mock_p4, 100)
        assert result == "(not found)"


class TestGraphIntegration:
    """Integration tests combining multiple graph functions."""
    
    def test_full_workflow_build_and_traverse(self):
        """Test building graph and then traversing it."""
        mock_p4 = Mock()
        mock_p4.run_changes.return_value = [
            {'change': '100', 'desc': 'Root', 'user': 'testuser'},
            {'change': '101', 'desc': 'Child 1\n\nDepends-On: 100', 'user': 'testuser'},
            {'change': '102', 'desc': 'Child 2\n\nDepends-On: 101', 'user': 'testuser'},
            {'change': '103', 'desc': 'Child 3\n\nDepends-On: 101', 'user': 'testuser'},
        ]
        
        # Build graph
        graph, child_to_parent, all_pending = build_stack_graph(mock_p4)
        
        # Verify all pending CLs are tracked
        assert all_pending == {100, 101, 102, 103}
        
        # Get stack for each CL
        assert get_stack_for_cl(100, child_to_parent) == [100]
        assert get_stack_for_cl(101, child_to_parent) == [100, 101]
        assert get_stack_for_cl(102, child_to_parent) == [100, 101, 102]
        
        # Traverse from base
        stack = get_stack_from_base(100, graph)
        assert 100 in stack
        assert 101 in stack
        assert 102 in stack
        assert 103 in stack
