Example of correct output

### Original Behavior (Incorrect)
```
CL 209: get_dataa() + 'raw_meh_data' + process_raw_data() [original]
CL 210: get_dataa() + 'raw_meh_data' + process_raw_data() + run_the_program() [depends on 209]
CL 211: get_dataa() + 'raw_meh_data' + process_raw_data() + run_the_program() + run_the_program() call [depends on 210]
```

### Expected Behavior (Correct)
After fixing 209 and running `p4-stack update 209 210 211`:
```
CL 209: get_data() + 'raw_data' + process_data() [fixed] ✓
CL 210: get_data() + 'raw_data' + process_data() + run_the_program() [has fix + its own changes] ✓
CL 211: get_data() + 'raw_data' + process_data() + run_the_program() + run_the_program() call [has fix + all changes] ✓
```
