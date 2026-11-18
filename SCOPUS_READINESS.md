# Scopus Readiness — Predictive SDN Traffic Engineering (2026-11-18)

## The plan
Campaign-03 adds 32 controlled runs (8 per system, frozen campaign-02
source and configuration) → pooled n = 16 repetitions per system,
~2,740 paired forecast samples. Campaign-02 stays untouched as the
frozen published batch; the pooled analysis is a new artifact
(`final-evaluation-artifacts/combined-16/`) produced by the project's
own analyzer, which re-validates that all 64 runs are
configuration-identical before computing anything.

## What the extra 32 runs actually buy
- **Tighter intervals:** at the same SD, 95% CI half-widths shrink by
  ~29% (1/√2). Claims currently resting on overlapping CIs (latency vs
  Reactive) get narrower intervals — nothing more is promised: whether
  they separate is an empirical outcome, and an equivalence claim
  would additionally need a pre-specified margin and an equivalence
  test. No power calculation has been done for any of these tests.
- **A sharper forecast test:** the valid same-target Diebold-Mariano
  comparison (ML vs persistence scored against identical actuals) is
  inconclusive at campaign-02 size (pooled DM −1.41, p = 0.157). More
  paired samples shrink the standard error; the per-run sign test
  (8/8 for GB on squared loss) would reach p < 0.001 at 15/16 or
  better purely by the test's own arithmetic — a statement about the
  test, not a promise about what the new data will show.
- **A harder congestion claim:** 0 congestion seconds across 16/16
  predictive-GB runs vs a positive mean for every other system is a
  much stronger headline than 8/8.

## What they do NOT buy (do not promise these)
- **Forecast MAE:** GB's +9.6 pt overprediction bias under load is
  structural (see WEAK_METRIC_AUDIT.md). More runs measure it more
  precisely; only a calibration-data retrain can move it.
- **The GNN verdict:** still needs the pooled all-link telemetry
  retrain, not more diamond4 runs.
- **Novelty:** runs add statistical strength, not contribution. The
  paper's novelty case is the conformal-gated predictive controller
  (held-out conformal coverage measured honestly at 0.81 against 90%
  nominal — the intervals undercover out of sample, reported as a
  result, not hidden), the intervention-censoring analysis of forecast
  evaluation, and the honest negative results — framed in RESEARCH.md.

## Venue reality check
Scopus indexes venues, not individual papers. Target a Scopus-indexed
IEEE/Springer conference or journal whose scope fits SDN/ML systems,
and check its current Scopus listing before submitting (listings
change). Reviewers at that level will expect: matched-configuration
repetitions (done, n=16 after campaign-03), confidence intervals and
significance tests (done after merge), threats to validity (drafted
in RESEARCH.md), and a public artifact (GitHub repo is live).

## Cost
32 runs × ~2.5–3.5 min in the campaign guest ≈ 1.5–2 hours,
sequential, resumable. One command on the Mac boots the VM and runs
the whole batch (`experiments/run_campaign_03_local.py`, using the
frozen source from campaign-02's `runtime-source.tar.gz`); one more
command merges (`experiments/merge_campaigns.py`).
