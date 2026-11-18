# Campaign 01 exclusion record

Campaign 01 remains preserved for debugging and provenance. It is excluded from final four-system conclusions.

All eight Gradient Boosting runs logged repeated `numpy._core` deserialization failures and produced no valid post-warmup predictions. The old `complete` metadata flag recorded traffic-generator completion, not successful learned inference. The standalone startup check read model metadata without exercising predictions.

The old analysis also used sender throughput, averaged unequal workload stages without duration weighting, and classified B-to-A recovery transitions as false congestion reroutes. Its network tables are historical outputs, not the final comparison.

The repaired campaign exports the same frozen model weights into a version-independent tree representation, verifies numerical parity on the host and VM, and smoke-tests actual Ryu execution. Receiver-side iperf JSON, causal measured RTT, interval CPU sampling, separate recovery transitions, and fail-closed prediction checks address the measurement and execution defects. All four systems are rerun under the repaired instrumentation. No campaign 01 or smoke-test observations enter the final aggregate.

The first repaired smoke capture (`campaign-02-smoke`) also failed and is preserved. Its gate detected a lazy module import that was incompatible with Ryu's restored import path. That capture is excluded too.
