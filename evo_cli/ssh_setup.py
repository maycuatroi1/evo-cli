"""SSH key setup functionality for EVO CLI."""
import os
import warnings

# Suppress deprecation warnings before importing paramiko
warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    from cryptography.utils import CryptographyDeprecationWarning
    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except ImportError:
    pass

import paramiko
import getpass
import sys
from pathlib import Path
import subprocess
import argparse

def connect_ssh(hostname, username, password):
    """Connect to SSH server using provided credentials."""
    try:
        # Create SSH client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect to server
        print(f"Connecting to {hostname} as {username}...")
        client.connect(hostname=hostname, username=username, password=password)
        
        print("Connection successful!")
        
        # Execute a simple command to verify connection
        stdin, stdout, stderr = client.exec_command("hostname")
        result = stdout.read().decode().strip()
        print(f"Server hostname: {result}")
        
        return client
    except Exception as e:
        print(f"Error connecting to SSH: {str(e)}")
        return None

def ensure_ssh_key_exists():
    """Generate SSH key if it doesn't exist."""
    ssh_dir = Path.home() / ".ssh"
    id_rsa_path = ssh_dir / "id_rsa"
    id_rsa_pub_path = ssh_dir / "id_rsa.pub"
    
    # Create .ssh directory if it doesn't exist
    os.makedirs(ssh_dir, exist_ok=True)
    
    # Check if SSH key already exists
    if id_rsa_path.exists() and id_rsa_pub_path.exists():
        print("SSH key pair already exists.")
        return id_rsa_path, id_rsa_pub_path
    
    # Generate SSH key pair
    print("Generating new SSH key pair...")
    try:
        subprocess.run(['ssh-keygen', '-t', 'rsa', '-b', '4096', '-f', str(id_rsa_path), '-N', ''], 
                      check=True, capture_output=True)
        print(f"SSH key pair generated at {id_rsa_path}")
        return id_rsa_path, id_rsa_pub_path
    except Exception as e:
        print(f"Error generating SSH key: {str(e)}")
        return None, None

def upload_ssh_key(client, pub_key_path):
    """Upload the public key to the remote server."""
    try:
        # Read public key content
        with open(pub_key_path, 'r') as f:
            pub_key_content = f.read().strip()
        
        # Create ~/.ssh directory on remote server if it doesn't exist
        stdin, stdout, stderr = client.exec_command("mkdir -p ~/.ssh")
        if stderr.read():
            print(f"Error creating .ssh directory: {stderr.read().decode()}")
            return False
        
        # Add the public key to authorized_keys
        stdin, stdout, stderr = client.exec_command(f"echo '{pub_key_content}' >> ~/.ssh/authorized_keys")
        if stderr.read():
            print(f"Error adding public key: {stderr.read().decode()}")
            return False
        
        # Set proper permissions
        stdin, stdout, stderr = client.exec_command("chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys")
        if stderr.read():
            print(f"Error setting permissions: {stderr.read().decode()}")
            return False
        
        print("SSH public key successfully uploaded to the server.")
        return True
    except Exception as e:
        print(f"Error uploading SSH key: {str(e)}")
        return False

def save_to_ssh_config(hostname, username, identity_file):
    """Save SSH credentials to ~/.ssh/config file."""
    try:
        # Get path to SSH config file
        ssh_dir = Path.home() / ".ssh"
        ssh_config_path = ssh_dir / "config"
        
        # Create .ssh directory if it doesn't exist
        os.makedirs(ssh_dir, exist_ok=True)
        
        # Prepare config entry
        config_entry = f"""
Host {hostname}
  HostName {hostname}
  User {username}
  IdentityFile {identity_file}
"""
        
        # Check if file exists and if the host is already configured
        if ssh_config_path.exists():
            with open(ssh_config_path, 'r') as f:
                content = f.read()
                if f"Host {hostname}" in content:
                    print(f"Host {hostname} already exists in SSH config. Not modifying the file.")
                    return
        
        # Append to existing file or create new one
        with open(ssh_config_path, 'a+') as f:
            f.write(config_entry)
            
        print(f"SSH config saved to {ssh_config_path}")
        print("You can now connect without a password using:")
        print(f"  ssh {hostname}")
        print("Or through VSCode by adding this in your SSH config.")
    except Exception as e:
        print(f"Error saving SSH config: {str(e)}")

def setup_ssh(args=None):
    """Main function to set up SSH with key-based authentication."""
    # Parse command line arguments if provided
    if args is None:
        parser = argparse.ArgumentParser(description='Set up SSH with key-based authentication.')
        parser.add_argument('-H', '--host', help='SSH server hostname or IP address')
        parser.add_argument('-u', '--user', help='SSH username')
        parser.add_argument('-p', '--password', help='SSH password (not recommended, use interactive mode instead)')
        parser.add_argument('-i', '--identity', help='Path to existing identity file to use')
        args = parser.parse_args()
    
    # Get credentials from arguments or prompt the user
    if args.host and args.user and args.password:
        # Use command line arguments
        hostname = args.host
        username = args.user
        password = args.password
        print(f"Using provided credentials for {username}@{hostname}")
    else:
        # Get user input for credentials
        print("Enter SSH connection details:")
        hostname = args.host or input("Hostname/IP: ").strip()
        username = args.user or input("Username (default: root): ").strip() or "root"
        password = args.password or getpass.getpass("Password: ")
        
        if not password:
            print("Password is required. Exiting.")
            return
    
    # Ensure SSH key pair exists or use provided identity
    if hasattr(args, 'identity') and args.identity:
        private_key_path = Path(args.identity)
        public_key_path = Path(f"{args.identity}.pub")
        if not private_key_path.exists() or not public_key_path.exists():
            print(f"Specified identity file {args.identity} not found or missing public key.")
            return
        print(f"Using existing identity file: {private_key_path}")
    else:
        private_key_path, public_key_path = ensure_ssh_key_exists()
        if not private_key_path or not public_key_path:
            print("Failed to ensure SSH key exists. Exiting.")
            return
    
    # Connect to SSH server
    client = connect_ssh(hostname, username, password)
    if not client:
        print("SSH connection failed. Exiting.")
        return
    
    # Upload SSH key to server
    if upload_ssh_key(client, public_key_path):
        # Close the connection
        client.close()
        # Save to SSH config with identity file
        save_to_ssh_config(hostname, username, str(private_key_path))
    else:
        client.close()
        print("Failed to set up passwordless authentication.")

def show_usage():
    """Show usage examples."""
    print("""
SSH Key Setup Script
===================

This script helps you set up SSH key-based authentication for password-less login.

Usage examples:
  1. Interactive mode:
     evo setupssh
  
  2. Command line mode:
     evo setupssh -H 42.96.16.233 -u root -p YourPassword
     
  3. Use existing identity file:
     evo setupssh -H 42.96.16.233 -u root -p YourPassword -i /path/to/private_key
""") 