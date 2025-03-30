"""Miniconda installation functionality for EVO CLI."""
import os
import platform
import subprocess
import tempfile
import sys
from pathlib import Path

def show_usage():
    """Show usage examples for Miniconda installation."""
    print("""
Miniconda Installation Script
============================

This command installs Miniconda on your system and configures your shell environment.

Usage:
  evo miniconda [options]

Options:
  -p, --prefix PATH    Installation directory (default: ~/miniconda3 or %USERPROFILE%\\miniconda3)
  -f, --force          Force installation even if Miniconda is already installed
  --help-examples      Show usage examples

Examples:
  evo miniconda                      # Install with default settings
  evo miniconda -p /custom/path      # Install to a custom path
  evo miniconda -f                   # Force reinstallation
""")

def is_windows():
    """Check if the current OS is Windows."""
    return platform.system() == "Windows"

def is_conda_installed(prefix):
    """Check if conda is already installed at the specified prefix."""
    conda_executable = os.path.join(prefix, "condabin", "conda.bat" if is_windows() else "conda")
    return os.path.exists(conda_executable)

def get_default_install_path():
    """Get the default installation path based on the OS."""
    if is_windows():
        return os.path.join(os.environ.get("USERPROFILE", ""), "miniconda3")
    else:
        return os.path.join(os.path.expanduser("~"), "miniconda3")

def install_miniconda_windows(prefix, force=False):
    """Install Miniconda on Windows."""
    if is_conda_installed(prefix) and not force:
        print(f"Miniconda is already installed at {prefix}")
        print("Use --force to reinstall")
        return True

    try:
        # Create temp directory for the installer
        with tempfile.TemporaryDirectory() as temp_dir:
            installer_path = os.path.join(temp_dir, "miniconda_installer.exe")
            
            # Download the installer
            print("Downloading Miniconda installer...")
            download_cmd = [
                "powershell", 
                "-Command", 
                f"Invoke-WebRequest -Uri https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe -OutFile {installer_path}"
            ]
            subprocess.run(download_cmd, check=True)
            
            # Run the installer silently
            print(f"Installing Miniconda to {prefix}...")
            install_cmd = [
                installer_path,
                "/InstallationType=JustMe",
                "/RegisterPython=0",
                "/S",
                "/D=" + prefix
            ]
            subprocess.run(install_cmd, check=True)
            
            # Add to PATH using setx
            bin_dir = os.path.join(prefix, "Scripts")
            condabin_dir = os.path.join(prefix, "condabin")
            
            # Get current PATH
            path_cmd = ["powershell", "-Command", "Write-Output $env:PATH"]
            result = subprocess.run(path_cmd, capture_output=True, text=True, check=True)
            current_path = result.stdout.strip()
            
            # Only add to PATH if not already present
            if bin_dir not in current_path and condabin_dir not in current_path:
                print("Adding Miniconda to PATH...")
                subprocess.run(["setx", "PATH", f"{bin_dir};{condabin_dir};{current_path}"], check=True)

            print("\nMiniconda has been installed successfully!")
            print("\nTo use conda, restart your terminal or run:")
            print(f"  {os.path.join(prefix, 'condabin', 'conda.bat')} init")
            return True
            
    except Exception as e:
        print(f"Error installing Miniconda: {str(e)}")
        return False

def install_miniconda_unix(prefix, force=False):
    """Install Miniconda on Linux/macOS."""
    if is_conda_installed(prefix) and not force:
        print(f"Miniconda is already installed at {prefix}")
        print("Use --force to reinstall")
        return True

    try:
        # Create temp directory for the installer
        with tempfile.TemporaryDirectory() as temp_dir:
            installer_path = os.path.join(temp_dir, "miniconda.sh")
            
            # Determine the correct installer based on platform
            if platform.system() == "Darwin":
                if platform.machine() == "arm64":
                    installer_url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh"
                else:
                    installer_url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh"
            else:  # Linux
                if platform.machine() == "aarch64":
                    installer_url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh"
                else:
                    installer_url = "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
            
            # Download the installer
            print("Downloading Miniconda installer...")
            download_cmd = ["wget", installer_url, "-O", installer_path]
            subprocess.run(download_cmd, check=True)
            
            # Make the installer executable
            subprocess.run(["chmod", "+x", installer_path], check=True)
            
            # Run the installer
            print(f"Installing Miniconda to {prefix}...")
            install_cmd = ["bash", installer_path, "-b", "-p", prefix]
            subprocess.run(install_cmd, check=True)
            
            # Add to PATH if needed
            shell_rc_file = os.path.expanduser("~/.bashrc")
            if platform.system() == "Darwin":
                if os.path.exists(os.path.expanduser("~/.zshrc")):
                    shell_rc_file = os.path.expanduser("~/.zshrc")
                
            # Check if already in PATH
            with open(shell_rc_file, "r") as f:
                content = f.read()
                if prefix not in content:
                    print(f"Adding Miniconda to PATH in {shell_rc_file}...")
                    with open(shell_rc_file, "a") as f:
                        f.write(f'\n# >>> conda initialize >>>\n')
                        f.write(f'export PATH="{prefix}/bin:$PATH"\n')
                        f.write(f'# <<< conda initialize <<<\n')
            
            print("\nMiniconda has been installed successfully!")
            print("\nTo use conda, restart your terminal or run:")
            print(f"  source {shell_rc_file}")
            return True
            
    except Exception as e:
        print(f"Error installing Miniconda: {str(e)}")
        return False

def install_miniconda(args):
    """Main function to install Miniconda based on the current OS."""
    # Get installation prefix
    prefix = args.prefix or get_default_install_path()
    
    # Install based on OS
    if is_windows():
        return install_miniconda_windows(prefix, args.force)
    else:
        return install_miniconda_unix(prefix, args.force) 