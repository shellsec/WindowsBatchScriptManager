param(
    [Parameter(Mandatory = $true)][string]$RequestPath,
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = 'Stop'
$account = 'wbsm-deploy'
$description = 'WindowsBatchScriptManager remote access'
$sshData = Join-Path $env:ProgramData 'ssh'
$marker = "# BEGIN WindowsBatchScriptManager remote access"
$endMarker = '# END WindowsBatchScriptManager remote access'
$managedBlock = '(?ms)^' + [regex]::Escape($marker) + '\r?\n.*?^' + [regex]::Escape($endMarker) + '(?=\r?$)'
$systemDirectory = [Environment]::SystemDirectory
$registry = 'HKLM:\SOFTWARE\WindowsBatchScriptManager\RemoteAccess'
$firewallDescription = 'Managed by WindowsBatchScriptManager remote access'

function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    $ErrorActionPreference = 'Continue'
    $output = & $Program @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed ($LASTEXITCODE): $($output -join ' ')"
    }
    $output
}

function Write-Result($Value) {
    $Value | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ResultPath -Encoding UTF8
}

function Save-ConnectionInfo([hashtable]$Info) {
    New-Item -Path $registry -Force | Out-Null
    New-ItemProperty -LiteralPath $registry -Name ConnectionInfo -PropertyType String -Force `
        -Value ($Info | ConvertTo-Json -Depth 5 -Compress) | Out-Null
}

function Get-MsiProductCode([string]$Installer) {
    $msi = New-Object -ComObject WindowsInstaller.Installer
    $flags = [Reflection.BindingFlags]::InvokeMethod
    $db = $msi.GetType().InvokeMember('OpenDatabase', $flags, $null, $msi, @($Installer, 0))
    $view = $db.GetType().InvokeMember('OpenView', $flags, $null, $db,
        @('SELECT `Value` FROM `Property` WHERE `Property` = ''ProductCode'''))
    $null = $view.GetType().InvokeMember('Execute', $flags, $null, $view, $null)
    $row = $view.GetType().InvokeMember('Fetch', $flags, $null, $view, $null)
    $code = $row.GetType().InvokeMember('StringData', [Reflection.BindingFlags]::GetProperty, $null, $row, @(1))
    if ($code -notmatch '^\{[0-9a-fA-F-]{36}\}$') { throw 'Could not identify the OpenSSH MSI product.' }
    return $code
}

function Get-OwnedOpenSsh([string]$Sshd) {
    $recorded = (Get-ItemProperty -LiteralPath $registry -Name OpenSshOwnership -ErrorAction SilentlyContinue).OpenSshOwnership
    if (-not $recorded -or -not $Sshd -or -not (Test-Path -LiteralPath $Sshd -PathType Leaf)) { return }
    $owner = $recorded | ConvertFrom-Json
    if ($owner.productCode -notmatch '^\{[0-9a-fA-F-]{36}\}$' -or $owner.servicePath -ine $Sshd) { return }
    $uninstallKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + $owner.productCode
    if ((Test-Path -LiteralPath $uninstallKey) -and (Get-FileHash -LiteralPath $Sshd).Hash -eq $owner.sha256) {
        return $owner
    }
}

function Get-OpenSshRemoval([string]$Sshd, [string]$BundledInstaller) {
    $capability = 'OpenSSH.Server~~~~0.0.1.0'
    $systemSshd = Join-Path $systemDirectory 'OpenSSH\sshd.exe'
    if ($Sshd -ieq $systemSshd) {
        $feature = Get-WindowsCapability -Online -Name $capability
        if ($feature.State -eq 'Installed') { return @{ capability = $capability } }
    }
    $owner = Get-OwnedOpenSsh $Sshd
    if ($owner) { return @{ productCode = $owner.productCode } }
    $msiSshd = Join-Path $env:ProgramFiles 'OpenSSH\sshd.exe'
    if ($Sshd -ieq $msiSshd -and $BundledInstaller -and (Test-Path -LiteralPath $BundledInstaller -PathType Leaf)) {
        $binarySignature = Get-AuthenticodeSignature -LiteralPath $Sshd
        $installerSignature = Get-AuthenticodeSignature -LiteralPath $BundledInstaller
        if ($binarySignature.Status -eq 'Valid' -and $installerSignature.Status -eq 'Valid' -and
            $binarySignature.SignerCertificate.Subject -match '(^|, )CN=Microsoft Corporation(,|$)' -and
            $installerSignature.SignerCertificate.Subject -match '(^|, )CN=Microsoft Corporation(,|$)') {
            $productCode = Get-MsiProductCode $BundledInstaller
            $product = Get-ItemProperty -LiteralPath ('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + $productCode) -ErrorAction SilentlyContinue
            if ($product.WindowsInstaller -eq 1 -and $product.DisplayName -match 'OpenSSH') {
                return @{ productCode = $productCode }
            }
        }
    }
    if ($Sshd) { throw 'Cannot identify this OpenSSH installation. Nothing was removed.' }
    return @{}
}

function Remove-ManagedAccess($Entry, [string]$Sshd) {
    $config = Join-Path $sshData 'sshd_config'
    if (Test-Path -LiteralPath $config) {
        $original = [IO.File]::ReadAllBytes($config)
        $settings = [IO.File]::ReadAllText($config)
        $matchesFound = [regex]::Matches($settings, $managedBlock)
        if ($matchesFound.Count -gt 1 -or ($settings.Contains($marker) -and $matchesFound.Count -ne 1)) {
            throw 'The WBSM SSH block could not be identified safely.'
        }
        if ($matchesFound.Count) {
            if ($Entry) { Set-AccountDisabled $Entry $true }
            try {
                [IO.File]::WriteAllText($config, [regex]::Replace($settings, $managedBlock, ''))
                if ($Sshd) {
                    Invoke-Checked $Sshd @('-t', '-f', $config) | Out-Null
                    if ((Get-Service sshd).Status -eq 'Running') { Restart-Service sshd }
                }
            } catch {
                [IO.File]::WriteAllBytes($config, $original)
                if ((Get-Service sshd -ErrorAction SilentlyContinue).Status -eq 'Running') { Restart-Service sshd }
                throw
            }
        }
    }
    if ($Entry) { Set-AccountDisabled $Entry $true }
    $rule = Get-NetFirewallRule -Name 'WBSM-OpenSSH-In-TCP' -ErrorAction SilentlyContinue
    if ($rule -and $rule.Description -eq $firewallDescription) { $rule | Remove-NetFirewallRule }
    $keyFile = Join-Path $sshData 'wbsm-deploy_authorized_keys'
    if ($Entry -and (Test-Path -LiteralPath $keyFile -PathType Leaf)) { Remove-Item -LiteralPath $keyFile }
    Save-ConnectionInfo @{ enabled = $false }
}

function Set-AccountDisabled([System.DirectoryServices.DirectoryEntry]$Entry, [bool]$Disabled) {
    $Entry.InvokeSet('AccountDisabled', @($Disabled))
    $Entry.CommitChanges()
}

function Set-WbsmStartup([bool]$StartWithApp) {
    Set-Service sshd -StartupType Manual
    $serviceRegistry = 'HKLM:\SYSTEM\CurrentControlSet\Services\sshd'
    New-ItemProperty -LiteralPath $serviceRegistry -Name WbsmStartWithApp -PropertyType DWord -Value ([int]$StartWithApp) -Force | Out-Null
}

function New-RandomBytes([int]$Count) {
    $bytes = New-Object byte[] $Count
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ,$bytes
}

function Find-Account([string]$Name) {
    Get-CimInstance Win32_UserAccount -Filter "LocalAccount=TRUE AND Name='$Name'"
}

function New-AccountName {
    for ($attempt = 0; $attempt -lt 32; $attempt++) {
        $name = 'wbsm-' + [BitConverter]::ToString((New-RandomBytes 2)).Replace('-', '').ToLowerInvariant()
        if (-not (Find-Account $name)) { return $name }
    }
    throw 'Could not allocate an unused account name.'
}

function Import-DeploymentKey([string]$PublicKey, $Sid, [string]$Keygen) {
    if ($PublicKey -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+={0,2}(?: [^\r\n]+)?$') {
        throw 'Choose a single OpenSSH public key (.pub).'
    }
    $temporaryKey = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($temporaryKey, $PublicKey + [Environment]::NewLine)
        $fingerprint = Invoke-Checked $Keygen @('-lf', $temporaryKey)
    } finally {
        Remove-Item -LiteralPath $temporaryKey -ErrorAction SilentlyContinue
    }
    $file = Join-Path $sshData 'wbsm-deploy_authorized_keys'
    if (-not (Test-Path -LiteralPath $file)) {
        New-Item -Path $file -ItemType File | Out-Null
        try {
            $icacls = Join-Path $systemDirectory 'icacls.exe'
            Invoke-Checked $icacls @($file, '/inheritance:r', '/grant:r', '*S-1-5-18:F',
                '*S-1-5-32-544:F', "*${Sid}:R") | Out-Null
            Invoke-Checked $icacls @($file, '/setowner', '*S-1-5-32-544') | Out-Null
        } catch {
            Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
            throw
        }
    }
    if ($PublicKey -notin (Get-Content -LiteralPath $file)) {
        [IO.File]::AppendAllText($file, [Environment]::NewLine + $PublicKey + [Environment]::NewLine)
    }
    return $fingerprint
}

function Rename-Account([string]$OldName, [string]$NewName) {
    if (-not ('WbsmAccountRename' -as [type])) {
        Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class WbsmAccountRename {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct UserInfo {
        [MarshalAs(UnmanagedType.LPWStr)] public string Name;
    }
    [DllImport("Netapi32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    private static extern int NetUserSetInfo(string server, string user, int level, ref UserInfo info, out int parameter);
    public static int Rename(string oldName, string newName) {
        UserInfo info = new UserInfo { Name = newName };
        int parameter;
        return NetUserSetInfo(null, oldName, 0, ref info, out parameter);
    }
}
'@
    }
    $status = [WbsmAccountRename]::Rename($OldName, $NewName)
    if ($status) { throw "Windows could not rename the account (status $status)." }
}

try {
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($request.action -eq 'service-startup') {
        if ($request.startWithApp -isnot [bool]) { throw 'A startup setting is required.' }
        Set-WbsmStartup $request.startWithApp
        Write-Result @{ startWithApp = $request.startWithApp }
        exit 0
    }
    if ($request.action -in 'service-start', 'service-stop', 'service-restart') {
        $service = Get-Service -Name sshd -ErrorAction SilentlyContinue
        if (-not $service) { throw 'OpenSSH Server is not installed. Enable remote access first.' }
        $targetStatus = 'Running'
        switch ($request.action) {
            'service-start' { Start-Service -InputObject $service }
            'service-stop' {
                Stop-Service -InputObject $service
                $targetStatus = 'Stopped'
            }
            'service-restart' { Restart-Service -InputObject $service }
        }
        $service.WaitForStatus($targetStatus, [TimeSpan]::FromSeconds(20))
        Write-Result @{ serviceStatus = $targetStatus }
        exit 0
    }
    if ((Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4) {
        throw 'This setup requires a local Windows account and cannot run on a domain controller.'
    }
    $managed = @(Get-CimInstance Win32_UserAccount -Filter "LocalAccount=TRUE AND Description='$description'")
    if ($managed.Count -gt 1) { throw 'More than one managed deployment account exists.' }
    $user = $managed | Select-Object -First 1
    if ($user) { $account = $user.Name }
    if ($request.action -notin 'enable', 'password', 'rename', 'disable', 'key', 'uninstall-ssh') { throw 'Unknown enrollment action.' }
    if ($request.action -in 'password', 'rename', 'key' -and (-not $user -or $user.Disabled)) {
        throw 'Enable remote access first.'
    }
    $previousName = $account
    if (-not $user -and $request.action -eq 'enable') { $account = New-AccountName }
    if ($request.action -eq 'rename') {
        $account = if ($request.name) { ([string]$request.name).ToLowerInvariant() } else { New-AccountName }
        if ($account -cnotmatch '^[a-z][a-z0-9-]{0,19}$') {
            throw 'Use 1 to 20 letters, digits or hyphens, starting with a letter.'
        }
        if ($account -ne $previousName -and (Find-Account $account)) { throw 'This Windows account name is already in use.' }
    }
    $entry = if ($user) {
        New-Object System.DirectoryServices.DirectoryEntry -ArgumentList "WinNT://$env:COMPUTERNAME/$previousName,user"
    }
    if ($request.action -eq 'disable') {
        if ($entry) { Set-AccountDisabled $entry $true }
        Save-ConnectionInfo @{ enabled = $false; account = $account }
        Write-Result @{ enabled = $false; account = $account; message = 'New logins are disabled. Close any existing SSH sessions separately.' }
        exit 0
    }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This release supports Windows x64.' }

    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if ($request.action -eq 'uninstall-ssh') {
        $sshd = if ($service -and $service.PathName -match '^\s*"?(.+?sshd\.exe)"?\s*$') {
            [Environment]::ExpandEnvironmentVariables($Matches[1])
        }
        if ($service -and -not $sshd) { throw 'Cannot identify this OpenSSH service. Nothing was removed.' }
        $removal = Get-OpenSshRemoval $sshd $request.installer
        Remove-ManagedAccess $entry $sshd
        $restartRequired = $false
        if ($removal.productCode) {
            $options = '/x {0} /qn /norestart' -f $removal.productCode
            $uninstall = Start-Process (Join-Path $systemDirectory 'msiexec.exe') -ArgumentList $options -Wait -PassThru
            if ($uninstall.ExitCode -notin 0, 3010) { throw "OpenSSH uninstall failed ($($uninstall.ExitCode)). WBSM access was removed." }
            $restartRequired = $uninstall.ExitCode -eq 3010
        } elseif ($removal.capability) {
            $feature = Remove-WindowsCapability -Online -Name $removal.capability
            $restartRequired = $feature.RestartNeeded -eq $true -or $feature.RestartNeeded -eq 'Yes'
        }
        if (-not $restartRequired -and (Get-Service sshd -ErrorAction SilentlyContinue)) {
            throw 'The OpenSSH service still exists. Removal did not complete.'
        }
        if ($entry) {
            $computer = New-Object System.DirectoryServices.DirectoryEntry -ArgumentList "WinNT://$env:COMPUTERNAME,computer"
            $computer.Children.Remove($entry)
        }
        Remove-ItemProperty -LiteralPath $registry -Name OpenSshOwnership,ConnectionInfo -ErrorAction SilentlyContinue
        Write-Result @{ enabled = $false; restartRequired = $restartRequired }
        exit 0
    }
    $installed = -not $service
    $existingFirewallRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
    if (-not $service) {
        if (-not $request.installer -or -not (Test-Path -LiteralPath $request.installer -PathType Leaf)) {
            throw 'This build has no offline OpenSSH MSI. Use the offline edition.'
        }
        $signature = Get-AuthenticodeSignature -LiteralPath $request.installer
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|, )CN=Microsoft Corporation(,|$)') {
            throw 'The OpenSSH MSI does not have a valid Microsoft signature.'
        }
        $productCode = Get-MsiProductCode $request.installer
        $installerArguments = '/i "{0}" ADDLOCAL=Server /qn /norestart' -f $request.installer
        $install = Start-Process -FilePath (Join-Path $systemDirectory 'msiexec.exe') -ArgumentList $installerArguments -Wait -PassThru
        if ($install.ExitCode -eq 3010) { throw 'Restart Windows to finish OpenSSH installation, then enable remote access again.' }
        if ($install.ExitCode -ne 0) { throw "OpenSSH installation failed: $($install.ExitCode)" }
        $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    }
    if (-not $service -or $service.PathName -notmatch '^\s*"?(.+?sshd\.exe)"?\s*$') {
        throw 'OpenSSH uses a custom service command. Configure this installation manually.'
    }
    $sshd = [Environment]::ExpandEnvironmentVariables($Matches[1])
    if ($installed) {
        $ownership = @{ productCode = $productCode; servicePath = $sshd; sha256 = (Get-FileHash -LiteralPath $sshd).Hash }
        New-Item -Path $registry -Force | Out-Null
        New-ItemProperty -LiteralPath $registry -Name OpenSshOwnership -PropertyType String -Force `
            -Value ($ownership | ConvertTo-Json -Compress) | Out-Null
    }
    Start-Service sshd
    $config = Join-Path $sshData 'sshd_config'
    $record = Join-Path $sshData 'wbsm-deploy.json'
    $original = [IO.File]::ReadAllBytes($config)
    $settings = [IO.File]::ReadAllText($config)
    $grants = if ($user -and (Test-Path -LiteralPath $record)) {
        Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    $matchesFound = [regex]::Matches($settings, $managedBlock)
    if ($matchesFound.Count -gt 1 -or ($settings.Contains($marker) -and $matchesFound.Count -ne 1)) {
        throw 'The existing remote access block could not be identified safely.'
    }
    $block = @"
$marker
Match User $account
    AuthenticationMethods publickey password
    PasswordAuthentication yes
    AuthorizedKeysFile __PROGRAMDATA__/ssh/wbsm-deploy_authorized_keys
    DisableForwarding yes
$endMarker
"@
    $newSettings = if ($matchesFound.Count) {
        [regex]::Replace($settings, $managedBlock, $block)
    } else {
        $settings.TrimEnd() + [Environment]::NewLine + [Environment]::NewLine + $block + [Environment]::NewLine
    }
    # Resolve the stock subsystem beside sshd instead of relying on the service PATH.
    $sftpServer = Join-Path (Split-Path $sshd) 'sftp-server.exe'
    if (Test-Path -LiteralPath $sftpServer -PathType Leaf) {
        $sftpLine = 'Subsystem sftp "{0}"' -f $sftpServer.Replace('\', '/')
        $newSettings = [regex]::Replace($newSettings,
            '(?im)^[ \t]*Subsystem[ \t]+sftp[ \t]+sftp-server(?:\.exe)?[ \t]*(?=\r?$)',
            $sftpLine)
    }
    $changedConfig = $false
    $renamed = $false
    $wasEnabled = $user -and -not $user.Disabled
    $newPassword = $null
    if (-not $user -or $request.action -eq 'password' -or $matchesFound.Count -eq 0 -or
        $matchesFound[0].Value -notmatch '(?m)^ +PasswordAuthentication yes\r?$') {
        $newPassword = if ($request.action -eq 'password' -and $null -ne $request.password) {
            [string]$request.password
        } else {
            "Aa1!" + [Convert]::ToBase64String((New-RandomBytes 9))
        }
        if ($newPassword.Length -eq 0 -or $newPassword -match '[\x00\r\n]') { throw 'Enter a valid password.' }
    }
    try {
        if (-not $user) {
            $computer = New-Object System.DirectoryServices.DirectoryEntry -ArgumentList "WinNT://$env:COMPUTERNAME,computer"
            $created = $computer.Children.Add($account, 'user')
            $created.Properties['Description'].Value = $description
            # Normal account, disabled until SSH configuration passes, password does not expire.
            $created.Properties['UserFlags'].Value = 0x10202
            $created.Invoke('SetPassword', @($newPassword)) | Out-Null
            $created.CommitChanges()
            $entry = $created
        }
        if ($user -and $account -ne $previousName) {
            Rename-Account $previousName $account
            $renamed = $true
            $entry = New-Object System.DirectoryServices.DirectoryEntry -ArgumentList "WinNT://$env:COMPUTERNAME/$account,user"
        }
        if ($newSettings -cne $settings) {
            [IO.File]::WriteAllText($config, $newSettings)
            $changedConfig = $true
        }
        Invoke-Checked $sshd @('-t', '-f', $config) | Out-Null
        $identity = New-Object Security.Principal.NTAccount -ArgumentList $env:COMPUTERNAME,$account
        $sid = $identity.Translate([Security.Principal.SecurityIdentifier]).Value
        $patterns = @('authenticationmethods publickey password', 'passwordauthentication yes',
            'authorizedkeysfile __PROGRAMDATA__/ssh/wbsm-deploy_authorized_keys', 'disableforwarding yes', 'subsystem sftp .+')
        $verification = 'Check SSH login and SFTP from the development machine.'
        try {
            $effective = (Invoke-Checked $sshd @('-T', '-f', $config, '-C', "user=$account,host=localhost,addr=127.0.0.1")) -join "`n"
        } catch {
            if ($_.Exception.Message -notmatch "ga_init, unable to resolve user $account(?:\s|$)") { throw }
            # The installer can resolve the SID even when sshd test mode cannot obtain a user token.
            $effective = (Invoke-Checked $sshd @('-T', '-f', $config)) -join "`n"
            $patterns = @('subsystem sftp .+')
            $verification = 'SSH syntax and the account SID passed. User-specific authentication still needs a real SSH login test.'
        }
        foreach ($pattern in $patterns) {
            if ($effective -notmatch "(?m)^$pattern\r?$") { throw "Existing SSH settings conflict with enrollment: $pattern" }
        }
        if ($installed -or (Get-OwnedOpenSsh $sshd)) {
            $defaultRule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
            if ($installed -and $defaultRule -and -not $existingFirewallRule) {
                $defaultRule | Set-NetFirewallRule -RemoteAddress LocalSubnet
            } elseif (-not $defaultRule -and
                -not (Get-NetFirewallRule -Name 'WBSM-OpenSSH-In-TCP' -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -Name 'WBSM-OpenSSH-In-TCP' -DisplayName 'WBSM OpenSSH (local subnet)' `
                    -Description $firewallDescription -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress LocalSubnet -Program $sshd | Out-Null
            }
        }
        if ($changedConfig) { Restart-Service sshd }
        $keygen = Join-Path (Split-Path $sshd) 'ssh-keygen.exe'
        $fingerprints = @(Get-ChildItem -LiteralPath $sshData -Filter 'ssh_host_*_key.pub' |
            ForEach-Object { Invoke-Checked $keygen @('-lf', $_.FullName) })
        $ports = @([regex]::Matches($effective, '(?m)^port (\d+)') | ForEach-Object { [int]$_.Groups[1].Value })
        $addresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } |
            Sort-Object SkipAsSource, InterfaceIndex |
            Select-Object -ExpandProperty IPAddress -Unique)
        if ($user -and $newPassword) { $entry.Invoke('SetPassword', @($newPassword)) | Out-Null }
        if ($request.action -eq 'enable' -and $null -ne $request.startWithApp) {
            Set-WbsmStartup $request.startWithApp
        } elseif ($installed) {
            Set-Service sshd -StartupType Manual
        }
        Set-AccountDisabled $entry $false
        $importedKey = if ($request.action -eq 'key') { Import-DeploymentKey $request.publicKey $sid $keygen }
        $connectionInfo = @{ enabled = $true; host = $env:COMPUTERNAME; addresses = $addresses; account = $account;
            ports = $ports; hostKeyFingerprints = $fingerprints; verification = $verification;
            directories = @($grants.directories | Where-Object { $_ }); services = @($grants.services | Where-Object { $_ }) }
        Save-ConnectionInfo $connectionInfo
        $connectionInfo.password = $newPassword
        $connectionInfo.importedKeyFingerprint = $importedKey
        Write-Result $connectionInfo
    } catch {
        try {
            if ($entry -and -not $wasEnabled) { Set-AccountDisabled $entry $true }
            if ($renamed) { Rename-Account $account $previousName }
        } finally {
            if ($changedConfig) {
                [IO.File]::WriteAllBytes($config, $original)
                Restart-Service sshd
            }
        }
        throw
    }
} catch {
    Write-Result @{ error = $_.Exception.Message }
    exit 1
}
