# Approved Calibration-02 Dataset

`telemetry_real_candidate.csv` is the quality-approved real Mininet/Ryu training source. Its provenance and sampling-gap report are stored alongside it.

The `prepared` directory contains the exact-timestamp feature table and the single chronological split used by Persistence, Linear Regression, Gradient Boosting Regressor, and GRU. The active `data/raw`, `data/processed`, and `data/splits` paths mirror this approved real-data run for compatibility with the training scripts.
