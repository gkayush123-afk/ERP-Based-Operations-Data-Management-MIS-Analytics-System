param(
    [string]$Database = "erp_mis_system",
    [string]$BackupDirectory = "C:\ProgramData\erp-mis\backups",
    [int]$RetentionDays = 14,
    [string]$DefaultsFile = "C:\ProgramData\erp-mis\mysql-backup.cnf",
    [string]$MysqldumpPath = "mysqldump.exe"
)

$ErrorActionPreference = "Stop"

if ($Database -notmatch '^[A-Za-z0-9_]+$') {
    throw "Database must contain only letters, numbers, and underscores."
}
if ($RetentionDays -lt 1) {
    throw "RetentionDays must be a positive integer."
}
if (-not (Test-Path -LiteralPath $DefaultsFile -PathType Leaf)) {
    throw "MySQL client credentials file not found: $DefaultsFile"
}

$null = New-Item -ItemType Directory -Path $BackupDirectory -Force
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$baseName = "${Database}_${stamp}"
$sqlTemporary = Join-Path $BackupDirectory "$baseName.sql.partial"
$gzipTemporary = Join-Path $BackupDirectory "$baseName.sql.gz.partial"
$backupPath = Join-Path $BackupDirectory "$baseName.sql.gz"
$stderrPath = Join-Path $BackupDirectory "$baseName.stderr.partial"

try {
    $arguments = @(
        "--defaults-extra-file=`"$DefaultsFile`"",
        "--single-transaction",
        "--quick",
        "--routines",
        "--triggers",
        "--events",
        "--hex-blob",
        "--no-tablespaces",
        "--default-character-set=utf8mb4",
        $Database
    )
    $process = Start-Process `
        -FilePath $MysqldumpPath `
        -ArgumentList $arguments `
        -NoNewWindow `
        -Wait `
        -PassThru `
        -RedirectStandardOutput $sqlTemporary `
        -RedirectStandardError $stderrPath
    if ($process.ExitCode -ne 0) {
        $errorText = if (Test-Path -LiteralPath $stderrPath) {
            Get-Content -LiteralPath $stderrPath -Raw
        } else {
            "mysqldump exited with code $($process.ExitCode)."
        }
        throw "Database dump failed: $errorText"
    }
    if (-not (Test-Path -LiteralPath $sqlTemporary) -or (Get-Item $sqlTemporary).Length -eq 0) {
        throw "mysqldump produced an empty backup."
    }

    $inputStream = [System.IO.File]::OpenRead($sqlTemporary)
    try {
        $outputStream = [System.IO.File]::Create($gzipTemporary)
        try {
            $gzipStream = [System.IO.Compression.GZipStream]::new(
                $outputStream,
                [System.IO.Compression.CompressionMode]::Compress
            )
            try {
                $inputStream.CopyTo($gzipStream)
            } finally {
                $gzipStream.Dispose()
            }
        } finally {
            $outputStream.Dispose()
        }
    } finally {
        $inputStream.Dispose()
    }

    $compressedInput = [System.IO.File]::OpenRead($gzipTemporary)
    try {
        $decompressor = [System.IO.Compression.GZipStream]::new(
            $compressedInput,
            [System.IO.Compression.CompressionMode]::Decompress
        )
        try {
            $nullStream = [System.IO.Stream]::Null
            $decompressor.CopyTo($nullStream)
        } finally {
            $decompressor.Dispose()
        }
    } finally {
        $compressedInput.Dispose()
    }

    Move-Item -LiteralPath $gzipTemporary -Destination $backupPath
    Remove-Item -LiteralPath $sqlTemporary -Force
    Get-ChildItem -LiteralPath $BackupDirectory -File -Filter "${Database}_*.sql.gz" |
        Where-Object { $_.LastWriteTimeUtc -lt [DateTime]::UtcNow.AddDays(-$RetentionDays) } |
        Remove-Item -Force
    Write-Output "Backup written: $backupPath"
} finally {
    foreach ($temporaryPath in @($sqlTemporary, $gzipTemporary, $stderrPath)) {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}
