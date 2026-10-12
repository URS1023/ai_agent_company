[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param([string]$CertificatePath = (Join-Path $PSScriptRoot 'Cloud-Brain-LAN-CA.cer'))
$ErrorActionPreference = 'Stop'
$certificatePathResolved = (Resolve-Path -LiteralPath $CertificatePath).Path
$certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new($certificatePathResolved)
$sha256 = [Security.Cryptography.SHA256]::Create()
try { $fingerprint = ([BitConverter]::ToString($sha256.ComputeHash($certificate.RawData))).Replace('-', '') }
finally { $sha256.Dispose() }
Write-Host "Certificate: $($certificate.Subject)"
Write-Host "SHA-256: $fingerprint"
Write-Host 'Verify this fingerprint with the server owner before continuing.'
Write-Host 'This adds trust for this LAN CA to the CURRENT Windows user only.'
Write-Host "Remove later with: Remove-Item 'Cert:\CurrentUser\Root\$($certificate.Thumbprint)'"
if ($PSCmdlet.ShouldProcess("Cert:\CurrentUser\Root - $($certificate.Subject)", 'Trust the displayed LAN certificate authority')) {
    Import-Certificate -FilePath $certificatePathResolved -CertStoreLocation 'Cert:\CurrentUser\Root' | Select-Object Subject, Thumbprint
}
