# Evaluation Protocol

## Split ownership

- Split at `source_document_id`; a document may occur in exactly one split.
- Every H/P/G variant from one `source_passage_id` inherits the same split.
- Run `audit_dataset_release.py` before training and before every release.
- Quarantine cross-split exact or near-duplicate candidates before training.

## Development decisions

Only the train and dev splits may be used for model fitting, early stopping,
threshold selection, class-weight selection, prompt revision, and hyperparameter
selection.

The test split is locked. It may be opened only for final reporting after the
configuration is frozen. Any change made after reading test results invalidates
that test run and requires a new untouched test set.

## Generator generalization

Report performance separately for each `generator_family::model_name`. A valid
unseen-generator test must contain at least one generator/model identity absent
from train. Use `build_unseen_generator_test.py`; it refuses identities already
seen in train.

Current status: **not ready**. The existing train, dev, and test P/G records all
belong to the Gemini family, and the test model also appears in train.

## Hybrid views

- Primary hybrid view: aligned contiguous P-light span inside H context (H-P-H).
- Require at least two H sentences on both sides and terminal punctuation at
  both provenance transitions.
- Random hybrids may be retained only as an auxiliary stress test and must be
  reported separately from the realistic-hybrid result.
