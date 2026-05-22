# retargeter_ml

External (non-MotionBuilder) training pipeline for the Retargeter option
recommender. **Empty for now** - the rule-based advisor lives inside
MotionBuilder (`Retargeter/core/option_advisor.py`) and is the current
production path.

This folder will be populated once enough `_retarget_feedback.jsonl` data
has accumulated to replace the rule thresholds with a learned model.

## Planned contents

- `requirements.txt` - pandas, scikit-learn / xgboost, joblib (separate
  venv from MoBu Python; nothing pyfbsdk-dependent lives here).
- `load_jsonl.py` - read every `_retarget_feedback.jsonl` under a root,
  resolve "current label" per take (latest `label` record wins).
- `featurise.py` - flatten `PairFeatures` to a feature matrix; align with
  the option output schema used by `OptionRecommendation`.
- `train.py` - multi-output classifier for boolean options + regressor
  for `plot_rate`; emits `Retargeter/data/option_recommender.joblib`.
- `predict.py` - stdin JSON features in, stdout JSON recommendation out;
  the MoBu-side `ModelBackedRecommender` runs this via `subprocess`.

## Why subprocess

MotionBuilder ships its own Python with limited control over installed
packages. Training and inference run in a normal venv elsewhere on disk
and the MoBu-side advisor calls the trainer venv's interpreter via
`subprocess.run([sys.executable, "-m", "retargeter_ml.predict", ...])`.
If the subprocess fails, the advisor falls back to the rule engine and
writes that into the reason panel.

## Feedback schema reference

See `Retargeter/core/feedback_log.py:TakeFeedback.to_record` for the
exact JSONL line format consumed by the loader.
