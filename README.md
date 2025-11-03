# p4-stack: Stacked Diffs for Perforce

**p4-stack** is a Python-based wrapper around the `p4` command to automates the manual process of updating, and submitting dependent changelists.

## How it works

### Fix the Stack

The typo is found in CL 209 and is the dependant of CL 210 and 211. Run:

```bash
$ p4-stack update 209 210 211
```

If a conflict occurs, the tool your editor, then you fix the typo, save, and quit.