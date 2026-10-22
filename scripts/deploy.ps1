
param(
    [Parameter(Mandatory)]
    [string]$Customer,

    [Parameter(Mandatory)]
    [string]$AppVersion,

    [Parameter(Mandatory)]
    [string]$AppCommit,

    [Parameter(Mandatory)]
    [string]$SourceExe,

    [Parameter(Mandatory)]
    [string]$SourceCustomerDirectory,

    [Parameter(Mandatory)]
    [string]$TargetDirectory,

    [string]$ConfigVersion = "unknown",
    [string]$ConfigCommit = "unknown",

    [string]$MaskVersion = "unknown",
    [string]$MaskCommit = "unknown"
)

$ErrorActionPreference = "Stop"

Import-Module powershell-yaml
. "$PSScriptRoot\Get-FolderHash.ps1"

# Validate source files and folders
if (!(Test-Path $SourceExe -PathType Leaf)) {
    throw "Executable not found: $SourceExe"
}

if (!(Test-Path $SourceCustomerDirectory -PathType Container)) {
    throw "Customer directory not found: $SourceCustomerDirectory"
}

$sourceMask = Join-Path $SourceCustomerDirectory "Mask"

if (!(Test-Path $sourceMask -PathType Container)) {
    throw "Mask folder not found: $sourceMask"
}

# Find YAML files directly and in subdirectories, excluding Mask
$yamlFiles = @(
    Get-ChildItem -LiteralPath $SourceCustomerDirectory `
        -Filter "*.yaml" -File -Recurse |
        Where-Object {
            $_.FullName -notlike "$sourceMask\*"
        }
)

if ($yamlFiles.Count -eq 0) {
    throw "No YAML files found in: $SourceCustomerDirectory"
}

# Prepare destination
New-Item -ItemType Directory -Force `
    -Path $TargetDirectory | Out-Null

$exeName = "myapp-$AppVersion.exe"
$targetExe = Join-Path $TargetDirectory $exeName
$targetConfig = Join-Path $TargetDirectory "Config"
$targetMask = Join-Path $TargetDirectory "Mask"

# Deploy executable
Copy-Item $SourceExe $targetExe -Force

# Deploy YAML files, preserving relative paths
if (Test-Path $targetConfig) {
    Remove-Item $targetConfig -Recurse -Force
}

New-Item -ItemType Directory -Force `
    -Path $targetConfig | Out-Null

$sourceRoot = (Resolve-Path $SourceCustomerDirectory).Path.TrimEnd('\', '/')

foreach ($file in $yamlFiles) {
    $relativePath = $file.FullName.Substring(
        $sourceRoot.Length
    ).TrimStart('\', '/')

    $destination = Join-Path $targetConfig $relativePath

    New-Item -ItemType Directory -Force `
        -Path (Split-Path $destination -Parent) | Out-Null

    Copy-Item -LiteralPath $file.FullName `
        -Destination $destination -Force
}

# Deploy Mask folder
if (Test-Path $targetMask) {
    Remove-Item $targetMask -Recurse -Force
}

Copy-Item $sourceMask -Destination $targetMask -Recurse -Force

# Calculate SHA-256 checksums
$exeHash = (Get-FileHash $targetExe -Algorithm SHA256).Hash
$configHash = Get-FolderHash -Path $targetConfig
$maskHash = Get-FolderHash -Path $targetMask

# Record deployment time in UTC
$deployedAt = [DateTime]::UtcNow.ToString("o")

# Build deployment manifest
$manifest = [ordered]@{
    customer = $Customer

    application = [ordered]@{
        version = $AppVersion
        artifact = $exeName
        git_commit = $AppCommit
        sha256 = $exeHash
    }

    configuration = [ordered]@{
        version = $ConfigVersion
        directory = "Config"
        file_count = $yamlFiles.Count
        git_commit = $ConfigCommit
        sha256 = $configHash
    }

    mask = [ordered]@{
        version = $MaskVersion
        directory = "Mask"
        git_commit = $MaskCommit
        sha256 = $maskHash
    }

    deployment = [ordered]@{
        environment = "production"
        deployed_at = $deployedAt
        status = "successful"
    }
}

# Write YAML manifest
$manifestPath = Join-Path $TargetDirectory "deployment.yaml"

$manifest |
    ConvertTo-Yaml |
    Set-Content -Path $manifestPath -Encoding utf8

# Append deployment history record
$historyPath = Join-Path $TargetDirectory "deployment-history.jsonl"

$historyRecord = [ordered]@{
    customer = $Customer
    app_version = $AppVersion
    app_commit = $AppCommit
    config_version = $ConfigVersion
    config_commit = $ConfigCommit
    config_file_count = $yamlFiles.Count
    mask_version = $MaskVersion
    mask_commit = $MaskCommit
    deployed_at = $deployedAt
    status = "successful"
    exe_sha256 = $exeHash
    config_sha256 = $configHash
    mask_sha256 = $maskHash
}

ConvertTo-Json -InputObject $historyRecord -Compress |
    Add-Content -Path $historyPath -Encoding utf8

Write-Host "Deployment completed."
Write-Host "Configuration files: $($yamlFiles.Count)"
Write-Host "Configuration SHA-256: $configHash"
Write-Host "Mask SHA-256: $maskHash"
Write-Host "Manifest: $manifestPath"
