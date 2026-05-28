# Computer Diagnoser

A premium system diagnostic and storage analysis tool for Windows. It provides a beautiful interface with live system monitoring, hardware health status, duplicate file finder, system cleanups, process management, network speed testing, and an interactive diagnostic AI chat engine.

## Features

- **System Intelligence Overview**: Real-time monitor for CPU, RAM, and Disk metrics.
- **Interactive AI Chat Engine**: Diagnoses issues in natural language based on your actual computer status.
- **Storage Intelligence**: Detailed breakdown of file types, folder sizes, large files, duplicates, and safe temp cleaner.
- **System Health & Security**: Battery wear report, SMART disk diagnostics, CPU/GPU temperatures, and Windows Event Viewer crash log analysis.
- **Optimization Tools**: System boost (RAM & Cache flush), startup program manager, process killer, and speed test.
- **Auto-Updater**: One-click updates that download, install, and relaunch automatically from GitHub Releases.

## Running in Development

1. Ensure you have Python 3.10 or newer installed.
2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the development startup script:
   ```bash
   start_diagnoser.bat
   ```

## Building the Executable

To compile the application into a single standalone `.exe` (which runs without requiring a Python installation):

Run the build script:
```bash
build_exe.bat
```
The compiled output will be generated at `dist/ComputerDiagnoser.exe`.

## CI/CD Pipeline

This repository is configured with a GitHub Actions workflow that automatically builds the production executable and publishes it as a GitHub Release whenever a new tag or update is pushed to the repository.
