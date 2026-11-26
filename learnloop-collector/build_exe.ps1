# Builds dist\learnloop-collector.exe (Stage 8). Run inside the project venv:
#   .\.venv\Scripts\Activate.ps1
#   .\build_exe.ps1
# PyInstaller only READS the project and writes to build\ and dist\.

python -m PyInstaller `
    --onefile `
    --name learnloop-collector `
    --paths src `
    --hidden-import watchdog.observers.read_directory_changes `
    --collect-all pypdfium2 `
    --collect-all pypdfium2_raw `
    --collect-submodules winrt `
    --noconfirm `
    run_collector.py
