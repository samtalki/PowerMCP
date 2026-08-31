# Egret MCP Server

MCP server for Egret power system optimization, enabling unit commitment, AC OPF, and DC OPF solutions.

## Requirements

- Python 3.10 or higher
- [Egret](https://github.com/grid-parity-exchange/Egret)
- Gurobi or ipopt solver for different tasks

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the MCP server:
```bash
python egret_mcp.py
```

Configure in your MCP client (e.g., Cursor, Claude Desktop):
```json
{
  "mcpServers": {
    "egret": {
      "command": "python",
      "args": ["Egret/egret_mcp.py"]
    }
  }
}
```

## Available Tools

- **solve_unit_commitment_problem(case_file, solver, mipgap, timelimit)**: Solve a unit commitment problem with custom solver, MIP gap, and time limits.
- **solve_ac_opf(case_file, solver, return_results)**: Run AC Optimal Power Flow on Matpower or Egret JSON case files.
- **solve_dc_opf(case_file, solver, return_results)**: Run DC Optimal Power Flow on Matpower or Egret JSON case files.
- **load_model_from_any(...)**: Convert a balanced case PowerIO can read, or a static `.pio.json` module, into an Egret model. The result includes structured `diagnostics` and, for stored input, `module` context.
- **load_model_from_json(...)**: Convert PowerIO balanced model JSON or a static `.pio.json` module without staging the source input. Inspect collections with PowerIO `list_states` and pass an `export_state` result to this tool.

## Prompt Example

Could you solve the DC OPF of `pglib_opf_case14_ieee.m` in Egret?

## Resources

- [Egret GitHub](https://github.com/grid-parity-exchange/Egret)
- [ipopt Documentation](https://coin-or.github.io/Ipopt/)
