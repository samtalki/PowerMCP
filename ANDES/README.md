# ANDES MCP Server

MCP server for ANDES (Python-based power system dynamic analysis), enabling power flow and time-domain simulation.

> **Note:** This MCP server is under active development and may need further modification to handle some internal code output and ensure full compatibility with all ANDES features.

## Requirements

- Python 3.10 or higher
- [ANDES](https://andes.readthedocs.io/)

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the MCP server:
```bash
python andes_mcp.py
```

Configure in your MCP client (e.g., Cursor, Claude Desktop):
```json
{
  "mcpServers": {
    "andes": {
      "command": "python",
      "args": ["ANDES/andes_mcp.py"]
    }
  }
}
```

## Available Tools

- **run_power_flow(file_path: str)**: Run power flow analysis on a power system case file.
- **run_time_domain_simulation(step_size: float = 0.01, t_end: float = 10.0)**: Run time domain simulation on the currently loaded power system.
- **run_eigenvalue_analysis(file_path: str)**: Run eigenvalue analysis on a power system case.
- **get_system_info()**: Get information about the currently loaded power system.
- **load_network_from_any(...)**: Convert any PowerIO-readable case or one selected `.pio.json` package state into the ANDES run format.
- **load_network_from_json(...)**: Convert PowerIO model JSON or one selected `.pio.json` package state without staging the source input.

## Prompt Example

Could you run power flow on the Kundur case at `yourpath\PowerMCP\ANDES\kundur_full.json` using ANDES and summarize the results? Then call `get_system_info` to show the system details.

## Resources

- [ANDES Documentation](https://andes.readthedocs.io/)
