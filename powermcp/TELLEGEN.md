# Native Tellegen solving and Studies

`powermcp run tellegen` exposes the installed Tellegen CLI directly. The browser
is optional. Build Tellegen with `cargo build -p tellegen-cli --features conic`
and set `POWERMCP_TELLEGEN_BINARY` (or `powermcp config set tellegen.binary`)
to its executable, or put `tellegen` on `PATH`. `powermcp doctor` runs
`tellegen capabilities` to confirm the binary speaks the contract.
`POWERIO_MCP_ALLOWED_ROOTS` applies to every input, output and Study path.

Everything Tellegen reads or writes is PowerIO IR generation 2. `solve` runs
one formulation (`dcpf`, `dcopf`, `acpf`, `socwr`) over a module given as
`powerio_ir` or as a grid exchange `path` that PowerIO parses in the server
process, with Tellegen's own `edits` and `sensitivities` request fields, and
bounds long arrays to `max_elements`. `solve_module` returns the stored
`powerio.DcOpfSolution` module, inline or written to `out_path` through a
staged write that refuses to overwrite. `plan` runs the bounded capacity
search for a `CapacityPlanSpec` and returns the proposal with its exact
proposed solution module. `capabilities` and `contract` describe the installed
build; `contract` carries the generated schemas for every request.

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
cancelled or times out, the adapter requests termination and allows 300 seconds
(`POWERMCP_TELLEGEN_CANCEL_GRACE_SECONDS`) for the current exact trial to finish and the cancelled planning record to save.
Inspect the saved revision before retrying. If that grace period expires, the
adapter kills the process and completed unsaved trials can be lost. A lock left by a terminated writer requires verifying that the
writer has exited before removing the lock. Configure a bounded solve budget to
keep operations within the five-minute process deadline.

The shared adapter tests cover path containment, rejection of agent apply
requests, exact revision forwarding and server registration. The native contract
check requires a compiled Tellegen executable; browser/headless numerical parity
is exercised by Tellegen's Study acceptance suite.
