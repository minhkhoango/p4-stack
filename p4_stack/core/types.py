"""
Central location for all shared type definitions, TypedDicts, and aliases.
"""
from typing import TypedDict

# --- P4 API Result Types ---
class RunChangeO(TypedDict):
    """
    An element of the list result when running p4 change -o, output new CL specs
    """
    Change: str      # new
    Client: str      # my_workspace
    User: str        # khoa
    Status: str      # new
    Description: str # <enter description here>\n


class RunChangesS(TypedDict):
    """
    An element of the list result when running p4 changes -s, create new CL
    """
    change: str      # 215
    time: str        # 1763123971
    user: str        # khoa
    client: str      # my_workspace
    Status: str      # pending
    changeType: str  # public
    shelved: str     # 
    desc: str        # Stack child 1\\n\\nDepends-On: 214\n


class RunDescribeS(TypedDict, total=False):
    """
    An element of the list result when running p4 describe -s, use to get CL desc
    """
    change: str
    user: str
    client: str
    time: str
    desc: str
    status: str


class RunPropertyL(TypedDict):
    """
    An element of the list result when running p4 property -l
    """
    name: str        # P4.Swarm.URL
    sequence: str    # 0
    value: str       # http://g15
    time: str        # 1760808902
    modified: str    # 2025/10/18 13:35:02
    modifiedBy: str  # swarm


class RunPrintMetaData(TypedDict):
    """
    The metadata portion of output from p4 print //...@=<CL>
    Even-indexed elements (0, 2, 4,...) in the p4.run_print list.
    """
    depotFile: str
    rev: str
    change: str
    action: str
    type: str
    time: str
    fileSize: str


class RunWhere(TypedDict):
    """
    An element of the list result when running p4 where PATH
    """
    depotFile: str   # //my_depot/file.txt
    clientFile: str  # //my_workspace/file.txt
    path: str        # /home/khoa/Breakthrough/Depot/file.txt

Snapshot = dict[str, str]
"""A mapping of a file's name to its str content for a CL."""

StackSnapshot = dict[int, Snapshot]
"""A mapping of a CL number to its complete file Snapshot."""

MergeResult = tuple[str, bool]
"""A tuple containing: (merged_content: str, has_conflict: bool)."""

FileToDepot = dict[str, str]
"""A mapping of a local filename to its full depot path."""

AdjacencyList = dict[int, list[int]]
"""A graph structure mapping a Parent CL to its list of direct Child CLs."""

ReverseLookup = dict[int, int]
"""A lookup map from a Child CL to its single Parent CL."""