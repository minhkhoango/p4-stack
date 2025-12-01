"""
Central location for all shared type definitions, TypedDicts, and aliases.
"""

from typing import Any, TypedDict, NotRequired


# --- P4 API Result Types ---
class RunChangeO(TypedDict):
    """
    An element of the list result when running p4 change -o, output new CL specs
    """

    Change: str  # new
    Client: str  # my_workspace
    User: str  # khoa
    Status: str  # new
    Description: str  # <enter description here>\n


class RunChangesS(TypedDict):
    """
    An element of the list result when running p4 changes -s, create new CL
    """

    change: str  # 215
    time: str  # 1763123971
    user: str  # khoa
    client: str  # my_workspace
    Status: str  # pending
    changeType: str  # public
    shelved: str  #
    desc: str  # Stack child 1\\n\\nDepends-On: 214\n


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


class RunDescribeSs(TypedDict):
    """
    Running p4 describe -S -s, use to check if CL is shelved
    """

    change: str  # '213'
    user: str  # 'khoa'
    client: str  # 'my_workspace'
    time: str  # '1763075492'
    desc: str  # 'Initial commit'
    status: str  # 'submitted'
    changeType: str  # 'public'
    path: str  # '//my_depot/*'
    oldChange: NotRequired[str]  # '209'
    depotFile: NotRequired[list[str]]  # ['//my_depot/file.txt']
    action: NotRequired[list[str]]  # ['add']
    type: NotRequired[list[str]]  # ['text']
    rev: NotRequired[list[str]]  # ['4', '2']
    fileSize: NotRequired[list[str]]  # ['77']
    digest: NotRequired[list[str]]  # ['A2C6...']


class RunPropertyL(TypedDict):
    """
    An element of the list result when running p4 property -l
    """

    name: str  # P4.Swarm.URL
    sequence: str  # 0
    value: str  # http://g15
    time: str  # 1760808902
    modified: str  # 2025/10/18 13:35:02
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
    An element of the list result when running p4.run_where PATH
    """

    depotFile: str  # //my_depot/file.txt
    clientFile: str  # //my_workspace/file.txt
    path: str  # /home/khoa/Breakthrough/Depot/file.txt


class RunTickets(TypedDict):
    """
    An element of the list result when running p4.run_tickets
    Used to get p4 ticket once logged in
    """

    Host: str  # localhost:1666
    User: str  # khoa
    Ticket: str  # long str ticket.


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


class RunSwarmPostEntry(TypedDict):
    """Detailed information for a single Swarm review post returned in API payloads."""

    id: int
    type: str
    changes: list[int]
    commits: list[int]
    author: str
    approvals: list[Any] | None
    participants: list[str]
    participantsData: dict[str, list[Any]]
    hasReviewer: int | bool
    description: str
    created: int
    updated: int
    projects: list[str]
    state: str
    stateLabel: str
    testStatus: str | None
    previousTestStatus: str
    testDetails: list[Any]
    deployStatus: str | None
    deployDetails: list[Any]
    pending: bool
    commitStatus: list[Any]
    groups: list[Any]
    complexity: Any | None
    versions: list[Any]


class RunSwarmPostData(TypedDict):
    """Container structure for the Swarm review payload."""

    review: list[RunSwarmPostEntry]


class RunSwarmPost(TypedDict):
    """TypedDict describing the JSON returned by Swarm review endpoints."""

    error: str | None
    messages: list[str]
    data: RunSwarmPostData


class RunSwarmGetEntry(TypedDict):
    """Detailed information for a single Swarm review entry returned by GET /reviews."""

    id: int  # 235
    type: str  # "default"
    changes: list[int]  # [214, 236]
    commits: list[int]  # []
    author: str  # "khoa"
    approvals: list[Any] | None  # null
    participants: list[str]  # ["khoa"]
    participantsData: dict[str, list[Any]]  # {"khoa": []}
    hasReviewer: int | bool  # 0
    description: str  # "Root_changelist\\n\\nDepends-On: 213\n"
    created: int  # 1764273634
    updated: int  # 1764273635
    projects: list[str]  # []
    state: str  # "needsReview"
    stateLabel: str  # "Needs Review"
    testStatus: str | None  # null
    previousTestStatus: str  # "testDetails"
    testDetails: list[Any]  # []
    deployStatus: str | None  # null
    deployDetails: list[Any]  # []
    pending: bool  # true
    commitStatus: list[Any]  # []
    groups: list[Any]  # []
    complexity: dict[str, int]  # {"files_modified":1,
    #  "lines_added":0,"lines_edited":3,"lines_deleted":0}


class RunSwarmGetData(TypedDict):
    """Container for the list of reviews and pagination counters returned by GET /reviews."""

    reviews: list[RunSwarmGetEntry]
    totalCount: int
    lastSeen: int


class RunSwarmGet(TypedDict):
    """TypedDict describing the GET /reviews response shape."""

    error: str | None
    messages: list[str]
    data: RunSwarmGetData
