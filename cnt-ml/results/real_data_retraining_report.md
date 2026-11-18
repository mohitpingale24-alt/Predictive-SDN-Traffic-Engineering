# Calibration-02 Real-Data Retraining Report

Status: **COMPLETE**

Approved source: `data/real/calibration-02/telemetry_real_candidate.csv`

Candidate predictive ML model: **Gradient Boosting Regressor**

Four-system evaluation: **AUTHORIZED**

## Data and split

- Approved candidate rows: 1,470, with 735 observations for each link.
- Engineered rows with exact timestamp t+5 labels: 1,428.
- Chronological, non-overlapping split shared by all models: 988 train, 204 validation, and 216 test rows.
- Boundary-crossing labels purged: 10 at the train/validation boundary and 10 at the validation/test boundary.
- Future-congestion observations at the 85% threshold: 18 train, 3 validation, and 3 test.
- Active feature schema contains 13 features and excludes `queue_occupancy`.

## Held-out test results

| Model | MAE (%) | RMSE (%) | R2 | Precision | Recall | F1 | Inference (ms/sample) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Persistence | 9.9170 | 22.3182 | 0.4249 | 0.0000 | 0.0000 | undefined | approximately 0 |
| Linear Regression | 14.2314 | 19.5673 | 0.5579 | undefined | 0.0000 | undefined | 0.0017 |
| Gradient Boosting | 11.9159 | 16.1071 | 0.7004 | 1.0000 | 1.0000 | 1.0000 | 0.0026 |
| GRU | **8.9502** | **14.9580** | **0.7417** | undefined | 0.0000 | undefined | 19.2572 |

## Candidate selection

Gradient Boosting is selected as the candidate for the final network experiment, not as the final winning model. Hyperparameters and the high-load sample weight were selected using validation data only, prioritizing congestion F1 and breaking ties by RMSE. The selected validation result was precision 0.75, recall 1.00, F1 0.8571, and RMSE 15.3293.

GRU has the best overall regression metrics, but it never predicts the fixed 85% congestion threshold on the held-out test set. Gradient Boosting is the only learned model that detects the three held-out congestion observations, while remaining much faster than GRU. This makes it the appropriate operational candidate for the controlled four-system comparison.

## Limitations and authorization boundary

The test set contains only three future-congestion observations. Perfect test precision, recall, and F1 therefore have wide uncertainty and must not be presented as general performance. Final model selection remains pending repeated network-level comparison of static, reactive, predictive Persistence, and predictive Gradient Boosting behavior.

The four-system evaluation is authorized because the real-data pipeline completed, artifacts match the active schema, and Gradient Boosting produces the controller's required high-congestion signal. Hyperparameter selection used validation data only; the held-out test results are reported as the final sanity check. Authorization does not imply that Gradient Boosting is the final model or that predictive routing has already improved network performance.
