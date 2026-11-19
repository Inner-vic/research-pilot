# Evaluation contract: a primary objective with diagnostic evidence

Use when agreeing success criteria, interpreting results or designing an evaluator.

## Agree before execution

Record the task, metric key/direction/target, fixed data/split/label map, checkpoint
selection and averaging definitions. Identify important classes/scenarios and any
explicitly agreed guardrails. Supporting metrics inform diagnosis; they do not
silently replace the primary objective or become invented hard stopping rules.
Missing measurements remain unknown. A higher aggregate can hide a class collapse.
A worse trial can be useful evidence; retain the incumbent and explain tradeoffs.

Classification: report class sample counts, precision, recall, F1 and confusion
matrix with explicit axes. "Per-class accuracy" is ambiguous: class hit rate is
recall; if one-vs-rest accuracy is shown, name its definition (TP+TN)/N. Preserve
class IDs when semantic names are unavailable. A zero denominator is undefined,
not an assumed zero. Compare the same classes and evaluation cohort.

Segmentation: the actual remote evaluator must report per-class intersection and
union or independently checked IoU, mIoU, pixel accuracy and any Dice evidence;
record ignore/background policy, class inclusion and empty-union convention.
Scene/range/small-object breakdowns and example overlays must link to registered
artifacts with sample identity, never fabricated illustrations. A classification
confusion matrix is not proof of a validated image evaluator. Detection/keypoint
metrics require their own registered thresholds and matching definitions.

## Decision and presentation

Keep the overview/trend on one primary metric. An experiment detail can expand
supporting metrics, class comparisons against its actual experimental baseline,
confusion and scenario details. Show units, support, deltas and evidence origin;
retain raw artifacts. For many classes use a searchable table, sorting by change
or support and a bounded matrix. Do not crowd the main lineage with every metric.

Author and independent reviewer must inspect available multidimensional evidence,
state the affected groups and uncertainty, and distinguish improving the average
from satisfying all agreed constraints. Missing decisive evidence leads to a
bounded evidence request/evaluator improvement rather than blind parameter search.

## Current implementation boundary

The production component-result/v1 collector and public .ts producer are explicitly
binary classification with validation.macro_f1 selection. They already archive
full validation metrics, curves and sample predictions. The display adapter derives
class P/R/F1, support and one-vs-rest accuracy from the verified confusion matrix;
it can format arbitrary square multiclass counts, but that does NOT qualify the
training/collection pipeline for multiclass or segmentation. Optional scalar IoU
fields are shown only when actually present in the verified artifact; current
binary training does not produce them. A new remote evaluator, artifact verifier,
goal/candidate adapter and real-task acceptance must be added before claiming a
new task type. Never migrate an active frozen study merely to update presentation.
