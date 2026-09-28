# Bypass execution policy
Set-ExecutionPolicy Bypass -Scope Process -Force

# Define URLs and file paths
$pythonInstaller = "python-3.12.3-amd64.exe"
$pythonInstallerPath = "./$pythonInstaller"
$pingScript = "ping.py"
$pythonPath = "C:\Python39"  # Adjust this path according to where Python is installed

# Uninstall ping3 library
pip uninstall -y ping3

# Remove ping.py script
Remove-Item -Path $pingScript -Force

# Remove Python installation directory
Remove-Item -Path $pythonPath -Recurse -Force

# Remove Python installer
Remove-Item -Path $pythonInstallerPath -Force
