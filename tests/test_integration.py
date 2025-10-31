# tests/test_integration.py
import pytest
import os
import uuid
from typer.testing import CliRunner
from p4_stack.main import app
from p4_stack.graph_utils import set_depends_on
from P4 import P4, P4Exception  # type: ignore[import-untyped]
from typing import Generator, Any, List, Optional

# --- Test Configuration ---
# These tests are marked 'integration' and are SKIPPED by default.
# Run them with: poetry run pytest -m integration
#
# They REQUIRE a live P4D server and the following env vars:
# P4PORT, P4USER, P4CLIENT
#
# The user's P4CLIENT workspace MUST be configured.
# ---

runner = CliRunner()

# --- Helper: Test Fixture ---

@pytest.fixture(scope="function")
def p4_test_env() -> Generator[P4, None, None]:
    """
    A pytest fixture that provides a live P4 connection and
    GUARANTEES cleanup after each test.
    """
    if not os.getenv("P4PORT") or not os.getenv("P4USER") or not os.getenv("P4CLIENT"):
        pytest.skip("P4 env vars (P4PORT, P4USER, P4CLIENT) not set.")

    # Clean up any state file from previous runs
    state_file = ".p4-stack-state.json"
    if os.path.exists(state_file):
        os.remove(state_file)

    p4 = P4()
    p4.connect()
    
    # Store changelists to delete
    pending_cls_to_delete: List[str] = []
    # Store test files to delete
    test_files_depot: List[str] = []

    try:
        # --- Provide the p4 object to the test ---
        yield p4
        
    finally:
        # --- Teardown Phase ---
        print("\n--- Integration Test Teardown ---")
        try:
            # 1. Revert all files (safely)
            try:
                p4.run('revert', '-k', '//...') # type: ignore[union-attr]  # -k: keep files, just revert
            except P4Exception as e:
                if "file(s) not opened" not in str(e):
                    print(f"Warning: could not revert files: {e}")
            
            # 2. Delete all created pending CLs
            # We fetch them just in case some were missed
            pending_changes: list[Any] = p4.run_changes('-s', 'pending', '-u', p4.user)  # type: ignore[union-attr]
            all_pending: list[str] = pending_cls_to_delete + [c['change'] for c in pending_changes]  # type: ignore[misc]
            
            for cl_num in set(all_pending): # Use set to avoid duplicates
                try:
                    # First, delete shelved files if any
                    try:
                        p4.run('shelve', '-d', '-c', cl_num)  # type: ignore[union-attr]
                    except P4Exception as e:
                        if "no shelved files" not in str(e).lower():
                            print(f"Warning: could not delete shelved files in CL {cl_num}: {e}")
                    
                    # Then delete the changelist
                    p4.run('change', '-d', cl_num)  # type: ignore[union-attr]
                except P4Exception as e:
                    if "no such changelist" not in str(e) and "already deleted" not in str(e):
                        print(f"Warning: could not delete pending CL {cl_num}: {e}")

            # 3. Delete test files from depot
            if test_files_depot:
                try:
                    try:
                        p4.run('revert', '//...')  # type: ignore[union-attr]  # Full revert
                    except P4Exception as e:
                        if "file(s) not opened" not in str(e):
                            raise
                    p4.run('delete', *test_files_depot)  # type: ignore[union-attr]
                    p4.run('submit', '-d', 'Test cleanup')  # type: ignore[union-attr]
                except P4Exception as e:
                    if "file(s) not on client" not in str(e):
                        print(f"Warning: Failed to delete test files: {e}")

        except P4Exception as e:
            print(f"TEARDOWN FAILED: {e}")
        
        # Clean up state file if it exists
        if os.path.exists(state_file):
            os.remove(state_file)
        
        p4.disconnect()  # type: ignore[union-attr]

# --- Helper: Test Setup Functions ---

def _create_test_file(p4: P4, filename: str) -> str:
    """Creates a new file, adds it, and returns its depot path."""
    ws_info: Any = p4.run_client('-o')[0]  # type: ignore[union-attr]
    ws_root: str = ws_info['Root']  # type: ignore[index]
    local_path: str = os.path.join(ws_root, filename)  # type: ignore[arg-type]
    
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, 'w') as f:
        f.write(f"Initial content for {filename}\n")
        
    p4.run('add', local_path)  # type: ignore[union-attr]
    where_result: list[Any] = p4.run('where', local_path)  # type: ignore[union-attr]
    return where_result[0]['depotFile']  # type: ignore[return-value]

def _submit_test_file(p4: P4, filename: str, content: str) -> str:
    """
    Creates, adds, and submits a file.
    **FIX:** Returns the depot_path, not the CL num.
    """
    depot_path: str = _create_test_file(p4, filename)
    
    # Edit the file with initial content
    local_path: str = p4.run('where', depot_path)[0]['path']  # type: ignore[union-attr, index]
    with open(local_path, 'w') as f:  # type: ignore[arg-type]
        f.write(content)
        
    p4.run('submit', '-d', f'Add {filename}')  # type: ignore[union-attr]
    return depot_path

def _create_shelved_cl(
    p4: P4,
    files_to_edit: List[str],
    desc: str,
    parent_cl_num: Optional[str] = None
) -> str:
    """
    Creates a new pending CL, edits files, and shelves it.
    **FIX:** Now accepts a parent_cl_num to unshelve from.
    """
    # 1. Create the pending CL
    change_spec: Any = p4.fetch_change()  # type: ignore[union-attr]
    change_spec['Description'] = desc
    cl_result: str = p4.save_change(change_spec)[0]  # type: ignore[union-attr, index]
    cl_num: str = cl_result.split()[1]  # type: ignore[union-attr]
    
    # 2. **FIX:** If it's a child, unshelve parent first
    if parent_cl_num:
        # Use -f to force unshelve and clobber writable files
        p4.run('unshelve', '-f', '-s', parent_cl_num, '-c', cl_num)  # type: ignore[union-attr]
            
    # 3. Edit files and move to CL
    for depot_path in files_to_edit:
        # **FIX:** This p4 edit may fail if parent didn't
        # include the file. We'll catch and ignore.
        try:
            p4.run('edit', '-c', cl_num, depot_path)  # type: ignore[union-attr]
        except P4Exception as e:
            if "not on client" not in str(e):
                raise e # Re-raise if it's not the error we expect
        
        # Edit the file
        local_path: str = p4.run('where', depot_path)[0]['path']  # type: ignore[union-attr, index]
        with open(local_path, 'a') as f:  # type: ignore[arg-type]
            f.write(f"\nChange from CL {cl_num}")
            
    # 4. Shelve
    p4.run('shelve', '-c', cl_num)  # type: ignore[union-attr]
    # Clean workspace safely - only revert if there are opened files
    try:
        p4.run('revert', '//...')  # type: ignore[union-attr]
    except P4Exception as e:
        if "file(s) not opened" not in str(e):
            raise
    return cl_num  # type: ignore[return-value]

# --- The Integration Tests ---

@pytest.mark.integration
def test_create_stack_integration(p4_test_env: P4) -> None:
    """Tests 'p4-stack create' moves files from default."""
    p4 = p4_test_env
    test_file: str = f"test_create_{uuid.uuid4().hex[:6]}.txt"
    depot_path: str = _create_test_file(p4, test_file)
    
    opened_default: list[Any] = p4.run_opened('-c', 'default')  # type: ignore[union-attr]
    assert any(f['depotFile'] == depot_path for f in opened_default)  # type: ignore[misc]
    
    desc: str = "Test create CL"
    result: Any = runner.invoke(app, ["create", desc])
    assert result.exit_code == 0
    
    changes: list[Any] = p4.run_changes('-s', 'pending', '-u', p4.user, '-m1')  # type: ignore[union-attr]
    new_cl_num: str = changes[0]['change']  # type: ignore[index]
    assert desc in changes[0]['desc']
    
    opened_new: list[Any] = p4.run_opened('-c', new_cl_num)  # type: ignore[union-attr, arg-type]
    assert any(f['depotFile'] == depot_path for f in opened_new)  # type: ignore[misc]
    assert not p4.run_opened('-c', 'default')  # type: ignore[union-attr]

@pytest.mark.integration
def test_list_stack_integration(p4_test_env: P4) -> None:
    """Tests 'p4-stack list' correctly builds and prints a stack."""
    p4 = p4_test_env
    
    file_a: str = f"test_list_{uuid.uuid4().hex[:6]}.txt"
    depot_a: str = _create_test_file(p4, file_a)
    
    cl_19: str = _create_shelved_cl(p4, [depot_a], "Base CL 19")
    desc_20: str = set_depends_on("Child CL 20", cl_19)
    # **FIX:** Pass parent CL to helper
    cl_20: str = _create_shelved_cl(p4, [depot_a], desc_20, parent_cl_num=cl_19)

    result: Any = runner.invoke(app, ["list"])
    
    assert result.exit_code == 0
    # The list command outputs to stderr, not stdout
    output: str = result.stderr if result.stderr else result.stdout
    assert f"► {cl_19}: Base CL 19" in output
    assert f"► {cl_20}: Child CL 20" in output

@pytest.mark.integration
def test_submit_stack_integration(p4_test_env: P4) -> None:
    """Tests 'p4-stack submit' submits linearly and patches descriptions."""
    p4 = p4_test_env
    
    file_a: str = f"test_submit_{uuid.uuid4().hex[:6]}.txt"
    depot_a: str = _create_test_file(p4, file_a)
    
    cl_19_pending: str = _create_shelved_cl(p4, [depot_a], "Submit Base 19")
    desc_20: str = set_depends_on("Submit Child 20", cl_19_pending)
    # **FIX:** Pass parent CL to helper
    cl_20_pending: str = _create_shelved_cl(
        p4, [depot_a], desc_20, parent_cl_num=cl_19_pending
    )

    result: Any = runner.invoke(app, ["submit", cl_19_pending], input="y\n")
    
    # Debug output
    if result.exit_code != 0:
        print(f"\nSubmit command failed with exit code: {result.exit_code}")
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        if result.exception:
            print(f"Exception: {result.exception}")
    
    assert result.exit_code == 0
    
    changes: list[Any] = p4.run_changes('-s', 'submitted', '-u', p4.user, '-m2')  # type: ignore[union-attr]
    cl_20_submitted: str = changes[0]['change']  # type: ignore[index]
    cl_19_submitted: str = changes[1]['change']  # type: ignore[index]
    
    assert f"Submitted as CL {cl_19_submitted}" in result.stdout
    assert f"Submitted as CL {cl_20_submitted}" in result.stdout
    
    desc_20_submitted: str = p4.run_describe(cl_20_submitted)[0]['desc']  # type: ignore[union-attr, index]
    assert f"Depends-On: {cl_19_submitted}" in desc_20_submitted
    
    pending: list[Any] = p4.run_changes('-s', 'pending', '-u', p4.user)  # type: ignore[union-attr]
    pending_nums: set[str] = {c['change'] for c in pending}  # type: ignore[misc]
    assert cl_19_pending not in pending_nums
    assert cl_20_pending not in pending_nums

@pytest.mark.integration
def test_update_stack_integration(p4_test_env: P4, mocker: Any) -> None:
    """Tests 'p4-stack update' (the "Disaster Path")."""
    p4 = p4_test_env
    
    file_a: str = f"test_update_{uuid.uuid4().hex[:6]}.txt"
    # **FIX:** Use the returned depot_path directly
    depot_a: str = _submit_test_file(p4, file_a, "Base content")

    cl_19: str = _create_shelved_cl(p4, [depot_a], "Update Base 19")
    desc_20: str = set_depends_on("Update Child 20", cl_19)
    # **FIX:** Pass parent CL to helper
    cl_20: str = _create_shelved_cl(p4, [depot_a], desc_20, parent_cl_num=cl_19)

    def mock_launch_editor(p4_conn: Any, cl_num: str) -> None:
        print(f"Mock Editor: Applying fix to CL {cl_num}")
        assert cl_num == cl_19
        local_path: str = p4.run_where(depot_a)[0]['path']  # type: ignore[union-attr, index]
        with open(local_path, 'a') as f:  # type: ignore[arg-type]
            f.write("\nFIX FROM EDITOR")
    
    mocker.patch("p4_stack.commands.update._launch_editor", mock_launch_editor)
    
    result: Any = runner.invoke(app, ["update", cl_19])
    
    # Debug output
    if result.exit_code != 0:
        print(f"\nUpdate command failed with exit code: {result.exit_code}")
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")
        if result.exception:
            print(f"Exception: {result.exception}")
    
    assert result.exit_code == 0
    assert f"Rebasing child [bold]{cl_20}[/bold]" in result.stdout
    assert "Stack update complete" in result.stdout
    
    # Clean up and verify the final state
    try:
        p4.run('revert', '//...')  # type: ignore[union-attr]
    except P4Exception as e:
        if "file(s) not opened" not in str(e):
            raise
    p4.run('unshelve', '-f', '-s', cl_20, '-c', cl_20)  # type: ignore[union-attr]
    local_path: str = p4.run_where(depot_a)[0]['path']  # type: ignore[union-attr, index]
    with open(local_path, 'r') as f:  # type: ignore[arg-type]
        content: str = f.read()
        
    print(f"Final content of {cl_20}:\n{content}")
    
    assert "Base content" in content
    assert f"Change from CL {cl_19}" in content
    assert f"Change from CL {cl_20}" in content
    assert "FIX FROM EDITOR" in content