# Native Tellegen Studies

`powermcp run tellegen` exposes the installed Tellegen CLI directly. The browser
is optional. Build Tellegen with `cargo build -p tellegen-cli --features conic`
and set `POWERMCP_TELLEGEN_BINARY` to its executable, or put `tellegen` on `PATH`.
`POWERIO_MCP_ALLOWED_ROOTS` applies to every Study input and output path.

Read `study_contract` for the installed build's generated Rust schemas and
formulation capabilities. `study_create` accepts `CreateStudy`, including the
PowerIO generation-2 input, outer objective and decision space. `study_run`
accepts a revision and one native `StudyOperation`: inspect, branch, revise a
goal, compare, propose or attach evidence. Capacity, demand placement and
redistribution use the same bounded search as the browser. Exact proposals stay
unapplied. An explicit user invocation of `tellegen study run PATH` can apply a
reviewed proposal with its state, base state, goal and revision binding.

`study_inspect` returns a compact continuation summary. Goal, state, experiment
and evidence queries return bounded JSON fragments with offsets; pin the
expected revision when reading several fragments. `study_export` validates the
bundle and returns its path, revision and SHA-256. Import that file into the
Tellegen Study panel or use `study_import` to create another filesystem copy.
Imported documents never restore approvals.

Native operations save atomically and refuse stale revisions. If the process is
cancelled or times out, inspect the saved revision before retrying: a completed
save may exist. A lock left by a terminated writer requires verifying that the
writer has exited before removing the lock. Configure a bounded solve budget to
keep operations within the five-minute process deadline.

The shared adapter tests cover path containment, rejection of agent apply
requests, exact revision forwarding and server registration. The native contract
check requires a compiled Tellegen executable; browser/headless numerical parity
is exercised by Tellegen's Study acceptance suite.
