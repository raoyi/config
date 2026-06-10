# Windows 端：通过 adb shell stdin 管道传输文件，不使用 adb push
# 文本文件自动检测编码并转换为 UTF-8；二进制文件自动回退到 base64
# 用法：.\adb_send.ps1 -FilePath input.txt -RemotePath /sdcard/output.txt

param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string]$RemotePath
)

if (-not (Test-Path $FilePath)) { Write-Error "找不到文件：$FilePath"; exit 1 }
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { Write-Error "找不到 adb"; exit 1 }

$bytes = [IO.File]::ReadAllBytes((Resolve-Path $FilePath))

# 检测是否含 null 字节（二进制文件特征）
$isBinary = $bytes -contains 0

Write-Host "文件：$FilePath  ($($bytes.Length) 字节)"
Write-Host "目标：$RemotePath"

if ($isBinary) {
    Write-Host "模式：base64（检测到二进制内容）"
    $b64 = [Convert]::ToBase64String($bytes)
    $b64 | & adb shell "base64 -d > '$RemotePath'"
} else {
    Write-Host "模式：直接传输（纯文本）"

    # 自动检测编码：优先 UTF-8，再尝试 GBK
    $detectedEncoding = $null
    foreach ($enc in @("UTF-8", "GB2312", "Latin1")) {
        try {
            $null = [Text.Encoding]::GetEncoding($enc).GetString($bytes)
            $detectedEncoding = $enc
            break
        } catch {}
    }
    Write-Host "检测到编码：$detectedEncoding"

    # 统一转换为 UTF-8 字节，换行符改为 LF
    $text     = [Text.Encoding]::GetEncoding($detectedEncoding).GetString($bytes)
    $text     = $text -replace "`r`n", "`n" -replace "`r", "`n"
    $utf8bytes = [Text.Encoding]::UTF8.GetBytes($text)

    # 写入临时文件再用 adb shell 读取，绕过 PowerShell 管道编码问题
    $tmp = [IO.Path]::GetTempFileName()
    [IO.File]::WriteAllBytes($tmp, $utf8bytes)

    # 用 cmd /c type 以二进制方式管道给 adb，避免 PowerShell 重新编码
    & cmd /c "type `"$tmp`" | adb shell `"cat > '$RemotePath'`""

    Remove-Item $tmp -Force
}

if ($LASTEXITCODE -ne 0) { Write-Error "传输失败"; exit 1 }

# 校验文件大小
$remoteSize = (& adb shell "wc -c < '$RemotePath'").Trim()
Write-Host "完成！远端：$remoteSize 字节 / 本地：$($bytes.Length) 字节"
