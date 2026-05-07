$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock] $Command,
        [Parameter(Mandatory = $true)]
        [string] $Description
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

Invoke-Native { python -m pip install -r requirements-build.txt } "Installing build requirements"

$PlaywrightBrowserRoot = Join-Path $env:LOCALAPPDATA "ms-playwright"
$HasChromium = (Test-Path -LiteralPath $PlaywrightBrowserRoot) -and
    [bool](Get-ChildItem -LiteralPath $PlaywrightBrowserRoot -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue)

if ($HasChromium) {
    Write-Host "Playwright Chromium already installed."
} else {
    Invoke-Native { python -m playwright install chromium } "Installing Playwright Chromium"
}

Invoke-Native { python -m PyInstaller --clean --noconfirm receipt_maker.spec } "Building executable"

$DistDir = Join-Path $ProjectRoot "dist\ReceiptGenerator"
Copy-Item -LiteralPath (Join-Path $ProjectRoot "filename_config.json") -Destination $DistDir -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "appsettings.json") -Destination $DistDir -Force

$InvoicesDir = Join-Path $DistDir "invoices"
if (-not (Test-Path -LiteralPath $InvoicesDir)) {
    New-Item -ItemType Directory -Path $InvoicesDir | Out-Null
}

$ReleaseReadme = @"
Receipt Generator

Run ReceiptGenerator.exe to open the app.

Keep this whole folder together. The _internal folder contains the bundled runtime and Chromium files required for PDF generation.

Edit filename_config.json to control optional filename fields. The receipt/invoice number is always included first.

Edit appsettings.json to control the business name, address, phone, email, and logo path shown on receipts.

Generated PDFs are saved in the invoices folder beside the executable.
"@
Set-Content -LiteralPath (Join-Path $DistDir "README.txt") -Value $ReleaseReadme -Encoding UTF8

$ExePath = Join-Path $DistDir "ReceiptGenerator.exe"
$SmokePdf = Join-Path $InvoicesDir "_packaged_smoke_test.pdf"
Remove-Item -LiteralPath $SmokePdf -Force -ErrorAction SilentlyContinue
$SmokeProcess = Start-Process -FilePath $ExePath -ArgumentList "--smoke-test" -WindowStyle Hidden -Wait -PassThru
if ($SmokeProcess.ExitCode -ne 0) {
    throw "Packaged executable smoke test failed with exit code $($SmokeProcess.ExitCode)."
}
if (-not (Test-Path -LiteralPath $SmokePdf)) {
    throw "Packaged executable smoke test did not create a PDF."
}
Remove-Item -LiteralPath $SmokePdf -Force

Write-Host ""
Write-Host "Executable created:"
Write-Host $ExePath
Write-Host ""
Write-Host "Keep appsettings.json and filename_config.json beside the exe so users can edit business and filename options."
