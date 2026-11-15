# Engineering contract

Step 9 creates deterministic S05/S06. Step 10 allows writes only in the
Story/E01 paths and hands `git.branch-change-request` to source control. Step 11
requires S07, Code Owner approval, `git.change-review-status`, and
`jenkins.implementation-checks`. Step 12 requires `git.merge-status`,
`jenkins.deterministic-acceptance`, and S08 before implementation `done`. Step
13 requires `jenkins.post-implementation-preflight` and S09 for geometry,
tensor, finite, tiny-set, checkpoint resume, and missing modality. Each external
receipt binds workspace, Experiment, revision/tree, pipeline digest, operation,
and requested acceptance subjects.
