"""Independent OS readback for the Windows private IO tests."""
from pathlib import Path
import subprocess
import os


def assert_private_acl(path: Path) -> None:
    # PowerShell uses .NET's independent ACL reader; product IO uses Win32.
    script = '''$ErrorActionPreference='Stop'
    $p=$env:LOOPX_PRIVATE_IO_TEST_PATH
    $acl=Get-Acl -LiteralPath $p
    if(-not $acl.AreAccessRulesProtected){throw 'DACL is inherited'}
    $owner=[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $allowed=@($owner,'S-1-5-18','S-1-5-32-544')
    foreach($ace in $acl.Access){
      $sid=$ace.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
      if($sid -notin $allowed -or $ace.IsInherited -or $ace.AccessControlType -ne 'Allow'){throw 'Unexpected private ACE'}
    }
    if($acl.Access.Count -ne 3){throw 'Private ACE set incomplete'}
    '''
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        env=dict(os.environ, LOOPX_PRIVATE_IO_TEST_PATH=str(path)),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
