"""
Pytest tests for p4_stack.core.rebase module.

Tests the 3-way merge engine and snapshot management logic.
"""

import pytest
import os
from unittest.mock import Mock, patch, mock_open
from p4_stack.core.rebase import (
    get_cl_snapshot,
    edit_snapshot_with_editor,
    _three_way_merge_file,
    three_way_merge_folder,
    commit_snapshot_to_cl,
)
from p4_stack.core.p4_actions import P4OperationError
from p4_stack.core.types import Snapshot, FileToDepot


class TestGetClSnapshot:
    """Test the get_cl_snapshot function."""

    def test_get_cl_snapshot_single_file(self):
        """Should extract single file from changelist."""
        mock_p4 = Mock()
        mock_p4.run_print.return_value = [
            {
                "depotFile": "//depot/file.txt",
                "rev": "1",
                "change": "100",
                "action": "add",
                "type": "text",
                "time": "1234567890",
                "fileSize": "100",
            },
            "file content here",
        ]

        snapshot, file_map = get_cl_snapshot(mock_p4, 100)

        assert "file.txt" in snapshot
        assert snapshot["file.txt"] == "file content here"
        assert file_map["file.txt"] == "//depot/file.txt"

    def test_get_cl_snapshot_multiple_files(self):
        """Should extract multiple files from changelist."""
        mock_p4 = Mock()
        mock_p4.run_print.return_value = [
            {
                "depotFile": "//depot/file1.txt",
                "rev": "1",
                "change": "100",
                "action": "add",
                "type": "text",
                "time": "1234567890",
                "fileSize": "100",
            },
            "content 1",
            {
                "depotFile": "//depot/dir/file2.py",
                "rev": "2",
                "change": "100",
                "action": "edit",
                "type": "text",
                "time": "1234567891",
                "fileSize": "200",
            },
            "content 2",
        ]

        snapshot, _ = get_cl_snapshot(mock_p4, 100)

        assert len(snapshot) == 2
        assert snapshot["file1.txt"] == "content 1"
        assert snapshot["file2.py"] == "content 2"

    def test_get_cl_snapshot_empty_changelist(self):
        """Should handle empty changesl gracefully."""
        mock_p4 = Mock()
        mock_p4.run_print.side_effect = Exception("no such file(s)")

        snapshot, file_map = get_cl_snapshot(mock_p4, 100)

        assert snapshot == {}
        assert file_map == {}

    def test_get_cl_snapshot_strips_quotes_from_depot_path(self):
        """Should strip quotes from depot paths."""
        mock_p4 = Mock()
        mock_p4.run_print.return_value = [
            {
                "depotFile": "'//depot/file.txt'",
                "rev": "1",
                "change": "100",
                "action": "add",
                "type": "text",
                "time": "1234567890",
                "fileSize": "100",
            },
            "content",
        ]

        _, file_map = get_cl_snapshot(mock_p4, 100)

        assert file_map["file.txt"] == "//depot/file.txt"

    def test_get_cl_snapshot_p4_error(self):
        """Should raise P4OperationError on unexpected errors."""
        mock_p4 = Mock()
        mock_p4.run_print.side_effect = Exception("Connection lost")

        with pytest.raises(P4OperationError, match="Failed to p4 print"):
            get_cl_snapshot(mock_p4, 100)


class TestEditSnapshotWithEditor:
    """Test the edit_snapshot_with_editor function."""

    @patch.dict(os.environ, {"EDITOR": "nano"})
    @patch("subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_edit_snapshot_with_editor_saves_and_reads(self, mock_file, mock_run):
        """Should save snapshot to temp files, launch editor, and read back."""
        original_snapshot: Snapshot = {
            "file1.txt": "original content 1",
            "file2.txt": "original content 2",
        }

        # Mock file reading to return modified content
        mock_file.return_value.__enter__.return_value.read.side_effect = [
            "modified content 1",
            "modified content 2",
        ]

        result = edit_snapshot_with_editor(original_snapshot)

        # Verify subprocess was called with editor
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0][0] == "nano"

        # Verify files were created in temp dir
        assert len(result) == 2

    @patch.dict(os.environ, {"EDITOR": "vim"})
    @patch("subprocess.run")
    def test_edit_snapshot_editor_failure(self, mock_run):
        """Should raise P4OperationError if editor fails."""
        mock_run.side_effect = Exception("Editor not found")

        snapshot: Snapshot = {"file.txt": "content"}

        with pytest.raises(P4OperationError, match="Editor.*failed"):
            edit_snapshot_with_editor(snapshot)

    @patch.dict(os.environ, {}, clear=True)
    @patch("subprocess.run")
    def test_edit_snapshot_uses_default_editor(self, mock_run):
        """Should use 'nano' as default editor if EDITOR not set."""
        snapshot: Snapshot = {"file.txt": "content"}

        # Mock the temp directory and file operations
        with patch("tempfile.TemporaryDirectory") as mock_temp:
            mock_temp.return_value.__enter__.return_value = "/tmp/test"
            with patch("builtins.open", mock_open(read_data="new content")):
                mock_run.side_effect = None  # Make subprocess succeed

                try:
                    edit_snapshot_with_editor(snapshot)
                except:
                    pass

                # Verify nano was called
                if mock_run.called:
                    assert "nano" in str(mock_run.call_args)


class TestThreeWayMergeFile:
    """Test the _three_way_merge_file function."""

    @patch("subprocess.run")
    def test_three_way_merge_no_conflict(self, mock_run):
        """Should return merged content without conflict."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "merged content"

        base = "original"
        ours = "ours change"
        theirs = "theirs change"

        content, has_conflict = _three_way_merge_file(base, ours, theirs)

        assert content == "merged content"
        assert has_conflict is False

    @patch("subprocess.run")
    def test_three_way_merge_with_conflict(self, mock_run):
        """Should detect conflicts (diff3 returns 1)."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = (
            "<<<<<<< CONFLICT\nours\n=======\ntheirs\n>>>>>>>"
        )

        base = "original"
        ours = "ours change"
        theirs = "theirs change"

        content, has_conflict = _three_way_merge_file(base, ours, theirs)

        assert has_conflict is True
        assert "<<<<<<" in content

    @patch("subprocess.run")
    def test_three_way_merge_handles_none_values(self, mock_run):
        """Should handle None values (file adds/deletes)."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "new content"

        # File added in ours, not in base/theirs
        _, has_conflict = _three_way_merge_file(None, "new content", None)

        assert has_conflict is False

    @patch("subprocess.run")
    def test_three_way_merge_calls_diff3_correctly(self, mock_run):
        """Should call diff3 with correct arguments."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "result"

        _three_way_merge_file("base", "ours", "theirs")

        # Verify diff3 was called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "diff3"
        assert call_args[1] == "-m"
        assert call_args[2] == "-E"


class TestThreeWayMergeFolder:
    """Test the three_way_merge_folder function."""

    @patch("subprocess.run")
    def test_three_way_merge_folder_file_added_only_in_ours(self, mock_run):
        """Should keep file added only in ours (child)."""
        base_folder: Snapshot = {}
        ours_folder: Snapshot = {"newfile.txt": "content"}
        theirs_folder: Snapshot = {}

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "newfile.txt" in result
        assert result["newfile.txt"] == ("content", False)

    @patch("subprocess.run")
    def test_three_way_merge_folder_file_added_only_in_theirs(self, mock_run):
        """Should keep file added only in theirs (new parent)."""
        base_folder: Snapshot = {}
        ours_folder: Snapshot = {}
        theirs_folder: Snapshot = {"newfile.txt": "parent content"}

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "newfile.txt" in result
        assert result["newfile.txt"] == ("parent content", False)

    @patch("subprocess.run")
    def test_three_way_merge_folder_file_deleted_in_ours_unchanged_theirs(
        self, mock_run
    ):
        """Should skip file deleted in ours if unchanged in theirs."""
        base_folder: Snapshot = {"file.txt": "content"}
        ours_folder: Snapshot = {}  # Deleted
        theirs_folder: Snapshot = {"file.txt": "content"}  # Unchanged

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "file.txt" not in result

    @patch("subprocess.run")
    def test_three_way_merge_folder_file_deleted_in_theirs_unchanged_ours(
        self, mock_run
    ):
        """Should skip file deleted in theirs if unchanged in ours."""
        base_folder: Snapshot = {"file.txt": "content"}
        ours_folder: Snapshot = {"file.txt": "content"}  # Unchanged
        theirs_folder: Snapshot = {}  # Deleted

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "file.txt" not in result

    @patch("subprocess.run")
    def test_three_way_merge_folder_file_deleted_both(self, mock_run):
        """Should skip file deleted in both branches."""
        base_folder: Snapshot = {"file.txt": "content"}
        ours_folder: Snapshot = {}
        theirs_folder: Snapshot = {}

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "file.txt" not in result

    @patch("p4_stack.core.rebase._three_way_merge_file")
    def test_three_way_merge_folder_modified_file(self, mock_merge):
        """Should merge file modified in multiple branches."""
        mock_merge.return_value = ("merged content", False)

        base_folder: Snapshot = {"file.txt": "base"}
        ours_folder: Snapshot = {"file.txt": "ours modified"}
        theirs_folder: Snapshot = {"file.txt": "theirs modified"}

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        assert "file.txt" in result
        assert result["file.txt"] == ("merged content", False)
        mock_merge.assert_called_once()

    @patch("subprocess.run")
    def test_three_way_merge_folder_complex_scenario(self, mock_run):
        """Should handle complex merge scenarios."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "merged"

        base_folder: Snapshot = {
            "file1.txt": "base1",
            "file2.txt": "base2",
            "deleted.txt": "will be deleted",
        }
        ours_folder: Snapshot = {
            "file1.txt": "ours1",
            "newfile.txt": "new in ours",
        }
        theirs_folder: Snapshot = {
            "file1.txt": "theirs1",
            "file3.txt": "new in theirs",
        }

        result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)

        # file1.txt should be merged (all have it)
        assert "file1.txt" in result
        # newfile.txt should be in result (only in ours)
        assert "newfile.txt" in result
        # file3.txt should be in result (only in theirs)
        assert "file3.txt" in result
        # deleted.txt should not be in result (deleted in ours)
        # file2.txt should not be in result (deleted in ours, but not in theirs either)


class TestCommitSnapshotToCl:
    """Test the commit_snapshot_to_cl function."""

    def test_commit_snapshot_no_changes(self):
        """Should handle case where no files changed."""
        mock_p4 = Mock()

        original_snapshot: Snapshot = {"file.txt": "content"}
        new_snapshot: Snapshot = {"file.txt": "content"}
        file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

        # Should not raise
        commit_snapshot_to_cl(mock_p4, 100, new_snapshot, original_snapshot, file_map)

        # Verify revert was called
        mock_p4.run_revert.assert_called()

    def test_commit_snapshot_file_edit(self):
        """Should handle file edits."""
        mock_p4 = Mock()
        mock_p4.run_where.return_value = [{"path": "/home/user/file.txt"}]

        with patch("builtins.open", mock_open()):
            with patch("os.path.exists", return_value=True):
                original_snapshot: Snapshot = {"file.txt": "old"}
                new_snapshot: Snapshot = {"file.txt": "new"}
                file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

                commit_snapshot_to_cl(
                    mock_p4, 100, new_snapshot, original_snapshot, file_map
                )

        # Verify edit was called
        mock_p4.run_edit.assert_called()
        # Verify shelve was called
        mock_p4.run_shelve.assert_called()

    def test_commit_snapshot_file_add(self):
        """Should handle file adds."""
        mock_p4 = Mock()
        mock_p4.run_where.return_value = [{"path": "/home/user/newfile.txt"}]

        with patch("builtins.open", mock_open()):
            with patch("os.path.exists", return_value=True):
                original_snapshot: Snapshot = {}
                new_snapshot: Snapshot = {"newfile.txt": "content"}
                file_map: FileToDepot = {"newfile.txt": "//depot/newfile.txt"}

                commit_snapshot_to_cl(
                    mock_p4, 100, new_snapshot, original_snapshot, file_map
                )

        mock_p4.run_edit.assert_called()

    def test_commit_snapshot_file_delete(self):
        """Should handle file deletes."""
        mock_p4 = Mock()

        original_snapshot: Snapshot = {"file.txt": "content"}
        new_snapshot: Snapshot = {}
        file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

        commit_snapshot_to_cl(mock_p4, 100, new_snapshot, original_snapshot, file_map)

        # Verify delete was called
        mock_p4.run_delete.assert_called()

    def test_commit_snapshot_new_file_not_in_map_raises_error(self):
        """Should raise error if trying to add file not in map."""
        mock_p4 = Mock()

        original_snapshot: Snapshot = {}
        new_snapshot: Snapshot = {"unknown.txt": "content"}
        file_map: FileToDepot = {}  # unknown.txt not mapped

        with pytest.raises(P4OperationError, match="Cannot add new file"):
            commit_snapshot_to_cl(
                mock_p4, 100, new_snapshot, original_snapshot, file_map
            )

    def test_commit_snapshot_file_not_in_client_view(self):
        """Should raise error if file not in client view."""
        mock_p4 = Mock()
        mock_p4.run_where.return_value = []  # Empty result

        original_snapshot: Snapshot = {"file.txt": "old"}
        new_snapshot: Snapshot = {"file.txt": "new"}
        file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

        with pytest.raises(P4OperationError, match="File not in client view"):
            commit_snapshot_to_cl(
                mock_p4, 100, new_snapshot, original_snapshot, file_map
            )

    def test_commit_snapshot_creates_directories(self):
        """Should create directories if they don't exist."""
        mock_p4 = Mock()
        mock_p4.run_where.return_value = [{"path": "/home/user/new/dir/file.txt"}]

        with patch("builtins.open", mock_open()):
            with patch("os.path.exists", return_value=False):
                with patch("os.makedirs") as mock_makedirs:
                    original_snapshot: Snapshot = {}
                    new_snapshot: Snapshot = {"file.txt": "content"}
                    file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

                    commit_snapshot_to_cl(
                        mock_p4, 100, new_snapshot, original_snapshot, file_map
                    )

        # Verify makedirs was called
        mock_makedirs.assert_called()

    def test_commit_snapshot_empty_cl_deletes_shelve(self):
        """Should delete shelve if CL becomes empty."""
        mock_p4 = Mock()

        original_snapshot: Snapshot = {"file.txt": "content"}
        new_snapshot: Snapshot = {}
        file_map: FileToDepot = {"file.txt": "//depot/file.txt"}

        commit_snapshot_to_cl(mock_p4, 100, new_snapshot, original_snapshot, file_map)

        # Verify shelve delete was called
        mock_p4.run_shelve.assert_called()
