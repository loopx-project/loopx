# Delegated Host lease deadline model

Run `lean -t 0 -DwarningAsError=true Deadline.lean` with the pinned toolchain.

`effectiveTime` represents how the supervisor carries forward elapsed time
between wall-clock samples. `acceptProof` represents the existing deadline
check and version-based update in the [delegated Host supervisor](../../../loopx/control_plane/turn_driver/leased_host_process.ts).
The model assumes a living supervisor and nonnegative elapsed ticks. Each
candidate proof must also pass the production identity, epoch and activity
checks. The model covers observed deadlines. The effect of machine suspend
on clock readings and process-group termination latency require runtime evidence.

The [integration test](../../control_plane_ts/host_process.test.ts) runs a real
child process and replaces only the supervisor's wall clock. It observes
whether the child writes a marker after readback of an unchanged proof.
