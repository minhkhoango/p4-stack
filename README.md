# p4-stack

A CLI for stacked diffs in Perforce, bringing a Git-like stacked change workflow (e.g., Gerrit) to P4.
Watch the 90s demo by me here: https://youtu.be/MKpt4zY4ptU

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
└── ► 220 (pending)
    ├── ► 221 (pending)
    └── ► 222 (pending)
```

### p4-stack update

Rebases an entire stack, starting from a base CL. It performs an in-memory, 3-way merge for every child CL, applying their changes on top of the new parent. If conflict, p4-stack prompts you to resolve conflict in editor just like git.

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
