# Script: Change gpt-image-2 default resolution from 4k to 1k

$canvasJsPath = "E:\running hub\Infinite-Canvas-main\static\js\canvas.js"
$backupPath = "E:\running hub\Infinite-Canvas-main\static\js\canvas.js.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

# Backup original file
Copy-Item -Path $canvasJsPath -Destination $backupPath -Force
Write-Host "Backup created: $backupPath" -ForegroundColor Green

# Read file content
$content = Get-Content -Path $canvasJsPath -Raw

# Regex pattern to match the function
$pattern = 'function defaultApiImageResolution\(model\)\s*\{[^}]*\}'
$newCode = 'function defaultApiImageResolution(model){ return "1k"; }'

if ($content -match 'function defaultApiImageResolution') {
    $content = [regex]::Replace($content, $pattern, $newCode)
    Set-Content -Path $canvasJsPath -Value $content -NoNewline -Encoding UTF8
    Write-Host "Success! Default resolution changed from 4k to 1k" -ForegroundColor Green
} else {
    Write-Host "Error: function defaultApiImageResolution not found" -ForegroundColor Red
}

Write-Host "`nPlease restart the server and refresh the browser." -ForegroundColor Cyan
