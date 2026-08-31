# Pandapower MCP Server

MCP server for pandapower transmission system analysis, enabling network creation, power flow, and contingency analysis.

## Requirements

- Python 3.10 or higher
- [pandapower](https://github.com/e2nIEE/pandapower)

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the MCP server:
```bash
python panda_mcp.py
```

Configure in your MCP client (e.g., Cursor, Claude Desktop):
```json
{
  "mcpServers": {
    "pandapower": {
      "command": "python",
      "args": ["pandapower/panda_mcp.py"]
    }
  }
}
```

## Available Tools

- **create_empty_network()**: Create an empty pandapower network.
- **load_network(file_path: str)**: Load a network from a `.json` file.
- **load_network_from_any(...)**: Load any balanced PowerIO case or stored static `.pio.json` module.
- **load_network_from_json(...)**: Load PowerIO model JSON or a stored static `.pio.json` module without staging a file.
- **export_network_to_format(to_format: str)**: Export the active network through `PioModule.emit`.
- **run_power_flow(algorithm, calculate_voltage_angles, max_iteration, tolerance_mva)**: Run power flow analysis (Newton-Raphson or Backward/Forward Sweep).
- **run_contingency_analysis(contingency_type, elements)**: Run N-1 or N-2 contingency analysis on lines and transformers.
- **get_network_info()**: Get statistics and data for buses, lines, transformers, generators, loads, and switches.

Collection modules must be exported to a static module through PowerIO before loading. Interchange tools return structured `diagnostics`; stored module context is returned under `module`.

## Prompt Example

Could you perform an N-1 contingency analysis using Pandapower on the case file `yourpath\PowerMCP\pandapower\test_case.json`? Based on the results, please provide suggestions for enhancing the system's security.

## Resources

- [Pandapower Documentation](https://pandapower.readthedocs.io/)
