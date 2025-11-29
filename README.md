# p4-stack

A CLI for stacked diffs in Perforce, bringing a Git-like stacked change workflow (e.g., Gerrit) to P4.

## Installation

```bash
$ pipx install p4-stack
```

## Commands

### p4-stack create

Creates a new stacked changelist.

#### Usage:

```bash
$ p4-stack create <PARENT_CL>
```

#### Example Output:

```bash
$ p4-stack create 220

Created new changelist: 222
Run 'p4 change 222' to add files and edit the description.
```

### p4-stack list

Lists all your pending stacks in a tree view.

#### Usage:

```bash
$ p4-stack list
```

#### Example Output:

```
Fetching pending changes for @khoa...
Current Stacks for khoa:
└── ► 220
    ├── ► 221
    └── ► 222
```

#### Consideration for future implementation:
Right now, list shows the relationship between CL, though it's missing the code review section, only showing local pending CLs, not if they have been uploaded to swarm, or approved
For instance, CL 220 can be shown to be pending locally, then uploaded to Swarm (CL 250 as unchangeble, and CL 248 as the version changeable using Swarm / p4v /..., both in **pending** status). Then, when CL 220 is commited, it shows new CL 252 as committed. A improved version can look like this:
```
Current Stacks for khoa:
# └── ► 220 (committed: 252)
├── ► 221 (local pending)
└── ► 222 (uploaded, rejected)
```

### p4-stack update

Rebases an entire stack, starting from a base CL. It performs an in-memory, 3-way merge for every child CL, applying their changes on top of the new parent. If conflict, p4-stack prompts you to resolve conflict in editor (nano as default).

Update command uses diff3 -m -E under the hood. This appears better than p4's default merge, but weaker and slower than git's ort merge strategy.

#### Usage:

```bash
$ p4-stack update <BASE_CL_TO_EDIT>
```

#### Example Output:

```bash
$ p4-stack update 214

CL 215 successfully rebased
CL 216 successfully rebased
In-memory rebase successful. Committing changes to Perforce...
Stack update complete for CL 214
```

#### Consideration for future implementation:
upadate command works well, but only in context of user's local pending changelist stack (214 -> 215 -> 216).
If some of the stack has been uploaded to swarm, when running p4-stack update 215, the changes won't be registered on the Swarm UI. When running p4-stack update 214, the new changes should be updated on Swarm UI, without creating a new stack of changelist (running p4-stack upload 214 automatically). 

### p4-stack upload

Right now, can only be submitted at the root of the stack. For each layer of the stack, a new review on swarm is created.

#### The good side
Integration with Swarm runs. Login to Swarm server requires p4 login -a -p ticket (which expires every 12 hours or so by default), and the navigation section between reviews works well, example of CL 215:
```
2nd changelist of the stack
Parent CL: Review 248

Create a new CL with 2 files
```

There currently stands some problem:
- When CL 215 (root), 216 (child 1), and 217 (child 2) are uploaded to swarm using p4-stack upload 215, the swarm UI will allow smooth direct commit of 215 (update file.txt from #3 to #4). However, conflict arise when trying to commit 216 and 217 (depot file.txt at #4, while base file.txt of CL 216 & 217 are at #3). This might be fine, since it practice, direct commit from Swarm is blocked, in favor of JetBrains TeamCity FIFO bot that processes submit (run tests & build) or Epic Games Robomerge. 
- Another problem is how the review UI does not currently show the wanted base version of files. For instance, let CL 214 be the committed base, and we have CL 215, 216, 217 stack on top. Then, when uploaded to swarm, we get CL 220 (root), 221 (child 1), 222 (child 2) crated by swarm@swarm user. Then CL 215 will display file.txt #3 vs file.txt potential #4. CL 216 display file.txt #3 vs file.txt potential #5. CL 217 display file.txt #3 vs file.txt potential #6.

#### Implementation plan
Exact details are quite unclear though the definition of done includes:
- Swarm UI actually display the diff between the 2 close member of the stack, not only commited vs current revision (AKA file.txt #5 vs file.txt #6 instead of file.txt #3 vs file.txt #6).
- Smooth integration with Robomerge / Horde / whaterver and Jetbrain Teamcity.
- In the future, when say p4-stack update <CL>, and CL is not the root, we can post a review of that specific CL, and squash the changes from all deeper layers. 
