# p4-stack: Stacked Diffs for Perforce

**p4-stack** is a Python-based wrapper around the `p4` command to automates the manual process of creating, managing, updating, and submitting dependent changelists.

## How it works

### 1. Create a Stack

Works as normal, but use `p4-stack create` instead of `p4 change`. It finds your files in the default changelist and stacks your new CL on the most recent one.

```bash
$ p4-stack create "feat: Add core refactor"
# Creates CL 19

$ p4-stack create "feat: Build new feature"
# Creates CL 20, automatically adding 'Depends-On: 19'

$ p4-stack create "docs: Add docs for new feature"
# Creates CL 21, automatically adding 'Depends-On: 20'

# Shelve your changes
$ p4 shelve -c 19
$ p4 shelve -c 20
$ p4 shelve -c 21
```

### 2. See Your Stacks

Use `p4-stack list` to get a view of all your dependent changes.

```bash
$ p4-stack list

Current Stacks:
  ► 19: feat: Add core refactor
    ► 20: feat: Build new feature
      ► 21: docs: Add docs for new feature
```

### 3. Fix the Stack

The typo is found in CL 19. Run:

```bash
$ p4-stack update 19
```

The tool opens your editor, then you fix the typo, save, and quit.

**p4-stack** automatically shelves your fix and then propagates it:

```
Rebasing child 20 onto 19... OK.
Rebasing child 21 onto 20... OK.
Stack update complete.
```

If a conflict occurs, the tool stops and instructs you to run `p4 resolve`, then `p4-stack update --continue` to resume.

### 4. Create a Review

Create a single Swarm review for the entire stack's changes.

```bash
$ p4-stack review 19
# Creates a temporary CL, unshelves 19, 20, and 21 into it,
```

### 5. Submit the Stack

Once approved, `p4-stack submit` will submit the entire stack as a clean, linear series of permanent changelists.

```bash
$ p4-stack submit 19

Submitting 19...
  -> Submitted as CL 25
Updating 20 to depend on 25...
Submitting 20...
  -> Submitted as CL 26
Updating 21 to depend on 26...
Submitting 21...
  -> Submitted as CL 27

Stack submitted successfully.
Delete 3 obsolete pending changelists? [y/N]: y
```

## Installation

### Steps

1. Install dependencies:
   ```bash
   $ poetry install
   ```

2. Run the tool using Poetry:
   ```bash
   $ poetry run p4-stack --help
   ```