# tests/test_integration.py
import pytest
import os
import uuid
from typer.testing import CliRunner
from p4_stack.main import app
from p4_stack.graph_utils import set_depends_on
from P4 import P4, P4Exception
from typing import Generator, Any, List

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

    p4 = P4()
    p4.connect()
    
    # Store changelists to delete
    pending_cls_to_delete: List[str] = []
    submitted_cls_to_revert: List[str] = []
    
    # Store test files to delete
    test_files_depot: List[str] = []

    try:
        # --- Provide the p4 object to the test ---
        yield p4
        
    finally:
        # --- Teardown Phase ---
        print("\n--- Integration Test Teardown ---")
        try:
            # 1. Revert all files
            p4.run('revert', '//...')
            
            # 2. Delete all created pending CLs
            for cl_num in pending_cls_to_delete:
                try:
                    p4.run('change', '-d', cl_num)
                except P4Exception as e:
                    if "no such changelist" not in str(e):
                        print(f"Warning: could not delete pending CL {cl_num}: {e}")

            # 3. Revert and delete submitted files
            for cl_num in submitted_cls_to_revert:
                try:
                    # Revert the change to make files editable again
                    p4.run('revert', f'//...@={cl_num},@={int(cl_num)-1}')
                except P4Exception as e:
                    print(f"Warning: could not revert submitted CL {cl_num}: {e}")

            # 4. Delete test files from depot
            if test_files_depot:
                p4.run('delete', *test_files_depot)
                p4.run('submit', '-d', 'Test cleanup')

        except P4Exception as e:
            print(f"TEARDOWN FAILED: {e}")
        
        p4.disconnect()

# --- Helper: Test Setup Functions ---

def _create_test_file(p4: P4, filename: str) -> str:
    """Creates a new file, adds it, and returns its depot path."""
    # Ensure clientFile path exists
    ws_info = p4.run_client('-o')[0]
    ws_root = ws_info['Root']
    local_path = os.path.join(ws_root, filename)
    
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, 'w') as f:
        f.write(f"Initial content for {filename}\n")
        
    p4.run('add', local_path)
    # Get depot path
    where_result = p4.run('where', local_path)
    return where_result[0]['depotFile']

def _submit_test_file(p4: P4, filename: str, content: str) -> str:
    """Creates, adds, and submits a file. Returns new CL num."""
    depot_path = _create_test_file(p4, filename)
    p4.run('submit', '-d', f'Add {filename}')
    result = p4.run_changes('-m1', f'{depot_path}#head')
    return result[0]['change']

def _create_shelved_cl(p4: P4, files_to_edit: List[str], desc: str) -> str:
    """Creates a new pending CL, edits files, and shelves it."""
    # 1. Create the pending CL
    change_spec = p4.fetch_change()
    change_spec['Description'] = desc
    cl_result = p4.save_change(change_spec)[0]
    cl_num = cl_result.split()[1]
    
    # 2. Edit files and move to CL
    for depot_path in files_to_edit:
        p4.run('edit', '-c', cl_num, depot_path)
        # Edit the file
        local_path = p4.run('where', depot_path)[0]['path']
        with open(local_path, 'a') as f:
            f.write(f"\nChange from CL {cl_num}")
            
    # 3. Shelve
    p4.run('shelve', '-c', cl_num)
    p4.run('revert', '//...') # Clean workspace
    return cl_num

# --- The Integration Tests ---

@pytest.mark.integration
def test_create_stack_integration(p4_test_env: P4):
    """Tests 'p4-stack create' moves files from default."""
    p4 = p4_test_env
    test_file = f"test_create_{uuid.uuid4().hex[:6]}.txt"
    depot_path = _create_test_file(p4, test_file)
    
    # File is now in the default changelist
    opened_default = p4.run_opened('-c', 'default')
    assert any(f['depotFile'] == depot_path for f in opened_default)
    
    # Run the command
    desc = "Test create CL"
    result = runner.invoke(app, ["create", desc])
    assert result.exit_code == 0
    
    # Find the new CL
    changes = p4.run_changes('-s', 'pending', '-u', p4.user, '-m1')
    new_cl_num = changes[0]['change']
    assert desc in changes[0]['desc']
    
    # Assert file was moved
    opened_new = p4.run_opened('-c', new_cl_num)
    assert any(f['depotFile'] == depot_path for f in opened_new)
    assert not p4.run_opened('-c', 'default')

@pytest.mark.integration
def test_list_stack_integration(p4_test_env: P4):
    """Tests 'p4-stack list' correctly builds and prints a stack."""
    p4 = p4_test_env
    
    # 1. Setup: Create a 2-CL stack
    file_a = f"test_list_{uuid.uuid4().hex[:6]}.txt"
    depot_a = _create_test_file(p4, file_a)
    
    cl_19 = _create_shelved_cl(p4, [depot_a], "Base CL 19")
    desc_20 = set_depends_on("Child CL 20", cl_19)
    cl_20 = _create_shelved_cl(p4, [depot_a], desc_20)

    # 2. Run
    result = runner.invoke(app, ["list"])
    
    # 3. Assert
    assert result.exit_code == 0
    assert f"► {cl_19}: Base CL 19" in result.stdout
    assert f"  ► {cl_20}: Child CL 20" in result.stdout

@pytest.mark.integration
def test_submit_stack_integration(p4_test_env: P4):
    """Tests 'p4-stack submit' submits linearly and patches descriptions."""
    p4 = p4_test_env
    
    # 1. Setup: Create a 2-CL stack
    file_a = f"test_submit_{uuid.uuid4().hex[:6]}.txt"
    depot_a = _create_test_file(p4, file_a)
    
    cl_19_pending = _create_shelved_cl(p4, [depot_a], "Submit Base 19")
    desc_20 = set_depends_on("Submit Child 20", cl_19_pending)
    cl_20_pending = _create_shelved_cl(p4, [depot_a], desc_20)

    # 2. Run
    result = runner.invoke(app, ["submit", cl_19_pending], input="y\n")
    
    # 3. Assert
    assert result.exit_code == 0
    
    # Find submitted CLs
    changes = p4.run_changes('-s', 'submitted', '-u', p4.user, '-m2')
    cl_20_submitted = changes[0]['change']
    cl_19_submitted = changes[1]['change']
    
    assert f"Submitted as CL {cl_19_submitted}" in result.stdout
    assert f"Submitted as CL {cl_20_submitted}" in result.stdout
    
    # Assert description was patched
    desc_20_submitted = p4.run_describe(cl_20_submitted)[0]['desc']
    assert f"Depends-On: {cl_19_submitted}" in desc_20_submitted
    
    # Assert pending are gone
    pending = p4.run_changes('-s', 'pending', '-u', p4.user)
    pending_nums = {c['change'] for c in pending}
    assert cl_19_pending not in pending_nums
    assert cl_20_pending not in pending_nums

@pytest.mark.integration
def test_update_stack_integration(p4_test_env: P4, mocker: Any):
    """Tests 'p4-stack update' (the "Disaster Path")."""
    p4 = p4_test_env
    
    # 1. Setup: Create base file and 2-CL stack
    file_a = f"test_update_{uuid.uuid4().hex[:6]}.txt"
    _submit_test_file(p4, file_a, "Base content")
    depot_a = p4.run_where(file_a)[0]['depotFile']

    cl_19 = _create_shelved_cl(p4, [depot_a], "Update Base 19")
    desc_20 = set_depends_on("Update Child 20", cl_19)
    cl_20 = _create_shelved_cl(p4, [depot_a], desc_20)

    # 2. Mock the editor to apply a fix
    def mock_launch_editor(p4_conn: Any, cl_num: str) -> None:
        print(f"Mock Editor: Applying fix to CL {cl_num}")
        assert cl_num == cl_19 # Ensure it's called on the base
        local_path = p4.run_where(depot_a)[0]['path']
        with open(local_path, 'a') as f:
            f.write("\nFIX FROM EDITOR")
    
    mocker.patch("p4_stack.commands.update._launch_editor", mock_launch_editor)
    
    # 3. Run
    result = runner.invoke(app, ["update", cl_19])
    
    # 4. Assert
    assert result.exit_code == 0
    assert f"Rebasing child [bold]{cl_20}[/bold]" in result.stdout
    assert "Stack update complete" in result.stdout
    
    # 5. Verify content of final rebased CL
    p4.run('revert', '//...')
    p4.run('unshelve', '-s', cl_20, '-c', cl_20) # Unshelve rebased CL
    local_path = p4.run_where(depot_a)[0]['path']
    with open(local_path, 'r') as f:
        content = f.read()
        
    print(f"Final content of {cl_20}:\n{content}")
    
    # It must contain all 4 changes in order
    assert "Base content" in content
    assert f"Change from CL {cl_19}" in content
    assert f"Change from CL {cl_20}" in content
    assert "FIX FROM EDITOR" in content