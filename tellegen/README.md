# Tellegen MCP server

The adapter passes stored PowerIO modules to a configured Tellegen CLI. The
CLI contract supplies every public request and response schema.

## Tools

`capabilities` returns Tellegen's formulation and differentiation capability
records.

`solve_dc_opf` accepts a `powerio.module/1` object containing a balanced network
or DC OPF instance. Its `results` field is the `dc_opf_solution` module returned
by Tellegen.

`plan_capacity` accepts the `plan_request` in Tellegen's contract: a PowerIO
module and `CapacityPlanSpec`. It uses implicit gradients to choose capacity
increase trials and exact DC OPF solves to accept a bounded proposal. The search
is heuristic. Its `results` field contains the `CapacityPlanOutcome` and exact
solution module for the amended instance. The input module is unchanged.

PowerMCP checks requests, responses, and the configured CLI against the bundled
`tellegen.cli/1` contract. A mismatched CLI is refused before an OPF command
runs.

## Build and configure Tellegen

Build `tellegen-cli` from a Tellegen checkout:

```bash
cargo build --release --locked -p tellegen-cli
powermcp config set tellegen.binary /path/to/tellegen/target/release/tellegen
```

`POWERMCP_TELLEGEN_BINARY` overrides the saved path. Run the server with
`powermcp run tellegen`. From a PowerMCP checkout, you can also run
`python tellegen/tellegen_mcp.py` when the environment variable is set.
