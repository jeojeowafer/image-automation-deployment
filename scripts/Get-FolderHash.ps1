
function Get-FolderHash {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (!(Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Folder not found: $Path"
    }

    $root = (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\', '/')

    # Hash each file using its relative path and content.
    # Sorting makes the result deterministic.
    $inventory = @(
        Get-ChildItem -LiteralPath $root -File -Recurse |
            ForEach-Object {
                $relativePath = $_.FullName.Substring(
                    $root.Length
                ).TrimStart('\', '/').Replace('\', '/')

                $fileHash = (
                    Get-FileHash -LiteralPath $_.FullName `
                        -Algorithm SHA256
                ).Hash

                "$relativePath`t$fileHash"
            } |
            Sort-Object -CaseSensitive
    )

    # Combine the inventory into a single SHA-256 hash.
    $content = ($inventory -join "`n") + "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $stream = [System.IO.MemoryStream]::new($bytes)

    try {
        return (
            Get-FileHash -InputStream $stream -Algorithm SHA256
        ).Hash
    }
    finally {
        $stream.Dispose()
    }
}
