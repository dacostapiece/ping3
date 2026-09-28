# Bypass execution policy
Set-ExecutionPolicy Bypass -Scope Process -Force

# Define URLs and file paths
$pythonUrl = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
$pythonInstaller = "python-3.12.3-amd64.exe"
$pingScriptUrl = "https://raw.githubusercontent.com/dacostapiece/ping3/main/ping.py"
$pingScript = "ping.py"

# Download the Python binary
Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonInstaller

# Install Python
Start-Process -Wait -FilePath .\$pythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1"

# Install pip (should be included with Python, but let's ensure it's there)
$pythonPath = "C:\Python39"  # Adjust this path according to where Python is installed
$env:Path += ";$pythonPath;$pythonPath\Scripts"
Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile "get-pip.py"
python get-pip.py

# Install ping3 library
pip install ping3

# Download the ping.py script
Invoke-WebRequest -Uri $pingScriptUrl -OutFile $pingScript

# Request target address from the user
$targetAddress = Read-Host "Please enter the target address (e.g., 8.8.8.8)"

# Run the ping.py script
python ping.py $targetAddress
