# Data model: what the integration reasons about

These are the entities this work manipulates. None of them is persisted application data; they are
the objects of the integration itself.

## Submission

A published, unmerged change set.

| Field | Meaning | Validation |
|---|---|---|
| id | The pull request number | Must be one the brief scopes, or reported as unlisted |
| head | The exact commit reviewed | Must equal the value recorded in the review; a moved head invalidates the combined verification |
| branch | Where the work lives | Reused for corrections; never duplicated into a new branch |
| checks | Independent check results for that head | Green is necessary, never sufficient |
| disposition | integrated / superseded-by-commit / open-with-reason | Must be recorded before the task closes |

State transitions: `open → reviewed → {corrected → reviewed}* → integrated | superseded | open-with-reason`.
A submission may never reach `integrated` while its content is absent from `main`.

## Finding

An observation made by the review.

| Field | Meaning | Validation |
|---|---|---|
| anchor | File and line | Required; a finding without one is an opinion |
| consequence | What goes wrong for a user or a maintainer | Required |
| resolution | fixed / accepted-with-reason / out-of-scope-with-reason | Required; silence is not a resolution |

## Combined tree

The single state produced by all accepted submissions together. It is the only state whose
verification counts for integration; three individually green branches are not evidence for their
combination, because they touch overlapping files.

## Uncovered path

A behaviour no available machine could exercise.

| Field | Meaning |
|---|---|
| capability | What is missing (GPU, private media, second toolchain) |
| exact operation | The command that would close it on an equipped machine |

An uncovered path is reported, never claimed as verified and never removed from the equipped CI job.
